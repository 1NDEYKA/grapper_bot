# pip install telethon

import os
import re
from typing import Union

from telethon import TelegramClient, events
from telethon.errors import UserAlreadyParticipantError, InviteHashInvalidError, InviteHashExpiredError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.utils import get_peer_id

# ==== НАСТРОЙКИ ====
api_id = 0000 # Ваш API_ID
api_hash = "USER_TOKEN" # Ваш API_HASH

CHANNELS_FILE = "channels.txt" # Файл со списком каналов
USERS_FILE = "users.txt"       # Файл со списком пользователей

client = TelegramClient("session", api_id, api_hash)

# === Состояние ===
waiting_for_channel = False
waiting_for_user = False
channels_peers: set[int] = set()
users: set[int] = set()
registered_peers: set[int] = set()

# === Работа с файлами ===
def load_list(filename: str) -> set[int]:
    if not os.path.exists(filename):
        return set()
    s = set()
    with open(filename, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    s.add(int(line))
                except ValueError:
                    pass
    return s

def save_list(filename: str, s: set[int]):
    with open(filename, "w", encoding="utf-8") as f:
        for x in sorted(s):
            f.write(str(x) + "\n")

# === Парсинг ===
def parse_channel_input(text: str):
    t = text.strip()
    if re.fullmatch(r"-?\d{5,}", t):
        return ("peer", int(t))
    if t.startswith("@"):
        return ("username", t[1:])
    if "t.me/" in t:
        m_inv = re.search(r"(?:joinchat/|\+)([A-Za-z0-9_-]+)", t)
        if m_inv:
            return ("invite", m_inv.group(1))
        name = t.split("t.me/", 1)[1]
        name = name.split("/", 1)[0]
        name = name.split("?", 1)[0]
        name = name.lstrip("@")
        return ("username", name)
    return ("username", t)

async def try_join_if_needed(kind: str, value: Union[str, int]):
    try:
        if kind == "invite":
            await client(ImportChatInviteRequest(value))
        elif kind in ("username", "peer"):
            await client(JoinChannelRequest(value))
    except UserAlreadyParticipantError:
        pass
    except InviteHashInvalidError:
        raise ValueError("Неверная или неподдерживаемая инвайт-ссылка.")
    except InviteHashExpiredError:
        raise ValueError("Инвайт-ссылка просрочена.")

async def resolve_peer_id(kind: str, value: Union[str, int]) -> int:
    if kind == "peer":
        return int(value)

    if kind == "invite":
        updates = await client(ImportChatInviteRequest(value))
        if hasattr(updates, "chats") and updates.chats:
            return get_peer_id(updates.chats[0])
        raise ValueError("Не удалось вступить в приватный канал по инвайту.")

    await try_join_if_needed(kind, value)
    entity = await client.get_entity(value)
    return get_peer_id(entity)

# === Подписка ===
def register_forward_handler(peer_id: int):
    if peer_id in registered_peers:
        return

    @client.on(events.NewMessage(chats=peer_id))
    async def _forwarder(ev):
        for uid in users:
            try:
                await client.forward_messages(uid, ev.message)
            except Exception as e:
                print(f"[!] Ошибка пересылки из {peer_id} для {uid}: {e}")

    registered_peers.add(peer_id)
    print(f"[*] Подписан на канал {peer_id}")

# === Добавление канала ===
async def add_channel_from_event(event) -> str:
    text = (event.raw_text or "").strip()
    fwd = getattr(event.message, "fwd_from", None)
    pid = None

    if fwd:
        if getattr(fwd, "channel_id", None):
            pid = int("-100" + str(fwd.channel_id))
        elif getattr(fwd, "from_id", None):
            pid = get_peer_id(await client.get_entity(fwd.from_id))
        if pid:
            if pid in channels_peers:
                return f"Канал уже добавлен ({pid})"
            channels_peers.add(pid)
            save_list(CHANNELS_FILE, channels_peers)
            register_forward_handler(pid)
            ent = await client.get_entity(pid)
            title = getattr(ent, "title", None) or getattr(ent, "username", None) or str(pid)
            return f"✅ Канал «{title}» добавлен по пересланному сообщению."

    if text:
        kind, value = parse_channel_input(text)
        pid = await resolve_peer_id(kind, value)
        if pid in channels_peers:
            return f"Канал уже добавлен ({pid})"
        channels_peers.add(pid)
        save_list(CHANNELS_FILE, channels_peers)
        register_forward_handler(pid)
        ent = await client.get_entity(pid)
        title = getattr(ent, "title", None) or getattr(ent, "username", None) or str(pid)
        return f"✅ Канал «{title}» добавлен по ссылке."

    return "❌ Не удалось определить канал. Перешли сообщение из него или пришли ссылку."

# === Команды ===
@client.on(events.NewMessage(pattern=r"^/add$"))
async def cmd_add(event):
    global waiting_for_channel
    if event.sender_id not in users:
        return
    waiting_for_channel = True
    await event.reply("Отправь ссылку/@username/-100… или пересылай сообщение из канала.")

@client.on(events.NewMessage(pattern=r"^/cancel$"))
async def cmd_cancel(event):
    global waiting_for_channel, waiting_for_user
    if event.sender_id not in users:
        return
    waiting_for_channel = False
    waiting_for_user = False
    await event.reply("Режим добавления выключен.")

@client.on(events.NewMessage(pattern=r"^/list$"))
async def cmd_list(event):
    if event.sender_id not in users:
        return
    lines = ["📌 Список каналов:"]
    if channels_peers:
        for pid in sorted(channels_peers):
            try:
                ent = await client.get_entity(pid)
                title = getattr(ent, "title", None) or getattr(ent, "username", None) or str(pid)
            except Exception:
                title = str(pid)
            lines.append(f"- {title} ({pid})")
    else:
        lines.append("❌ Список пуст.")

    lines.append("\n👥 Список пользователей:")
    if users:
        for uid in sorted(users):
            lines.append(f"- {uid}")
    else:
        lines.append("❌ Никого нет.")
    await event.reply("\n".join(lines))

@client.on(events.NewMessage(pattern=r"^/help$"))
async def cmd_help(event):
    if event.sender_id not in users:
        return
    text = (
        "🤖 Команды бота:\n\n"
        "/add – добавить новый канал\n"
        "/cancel – отмена режима добавления\n"
        "/list – список каналов и пользователей\n"
        "/remove – удалить канал\n"
        "/adduser – добавить получателя (ID)\n"
        "/removeuser – удалить получателя (ID)\n"
        "/help – показать справку\n"
    )
    await event.reply(text)

# === Удаление канала ===
remove_mode = False

@client.on(events.NewMessage(pattern=r"^/remove$"))
async def cmd_remove(event):
    global remove_mode
    if event.sender_id not in users:
        return
    if not channels_peers:
        await event.reply("Список каналов пуст.")
        return
    remove_mode = True
    await event.reply("Отправь peer_id канала для удаления.")

@client.on(events.NewMessage)
async def handle_modes(event):
    global waiting_for_channel, waiting_for_user, remove_mode
    if event.sender_id not in users:
        return

    text = (event.raw_text or "").strip()
    if text.startswith("/"):
        return  # игнорируем команды

    if waiting_for_channel:
        waiting_for_channel = False
        try:
            msg = await add_channel_from_event(event)
            await event.reply(msg)
        except Exception as e:
            await event.reply(f"❌ Ошибка: {e}")

    elif waiting_for_user:
        waiting_for_user = False
        try:
            uid = int(text)
            users.add(uid)
            save_list(USERS_FILE, users)
            await event.reply(f"✅ Пользователь {uid} добавлен.")
        except Exception:
            await event.reply("❌ Нужно отправить число (ID).")

    elif remove_mode:
        remove_mode = False
        try:
            pid = int(text)
            if pid not in channels_peers:
                await event.reply("❌ Такого канала нет.")
                return
            channels_peers.discard(pid)
            save_list(CHANNELS_FILE, channels_peers)
            await event.reply(f"🗑 Канал {pid} удалён.")
        except Exception:
            await event.reply("❌ Нужно отправить число (peer_id).")


# === Добавление/удаление пользователей ===
@client.on(events.NewMessage(pattern=r"^/adduser$"))
async def cmd_adduser(event):
    global waiting_for_user
    if event.sender_id not in users:
        return
    waiting_for_user = True
    await event.reply("Отправь ID пользователя (число).")

@client.on(events.NewMessage(pattern=r"^/removeuser$"))
async def cmd_removeuser(event):
    if event.sender_id not in users:
        return
    try:
        uid = int(event.raw_text.strip().split(maxsplit=1)[1])
        if uid not in users:
            await event.reply("❌ Такого пользователя нет.")
            return
        users.discard(uid)
        save_list(USERS_FILE, users)
        await event.reply(f"🗑 Пользователь {uid} удалён.")
    except Exception:
        await event.reply("❌ Используй: /removeuser <ID>")

# === Запуск ===
async def bootstrap():
    channels_peers.update(load_list(CHANNELS_FILE))
    users.update(load_list(USERS_FILE))
    for pid in sorted(channels_peers):
        register_forward_handler(pid)
    print(f"[*] Каналов: {len(channels_peers)}, пользователей: {len(users)}")

if __name__ == "__main__":
    client.start()
    client.loop.run_until_complete(bootstrap())
    print("Бот запущен. Команды: /add, /list, /remove, /adduser, /removeuser, /help")
    client.run_until_disconnected()
