// WhatsApp Web bridge for the Velox demo.
//
// Logs in to a normal WhatsApp account (scan the QR code once), passes every incoming
// text to the Python bot at VELOX_URL/bridge/message and sends back its reply.
// The bot sends owner alerts and reminders through POST /send on this bridge.
//
// This is unofficial: WhatsApp may ban the number. Use a spare SIM, never a client's number.

const http = require("node:http");
const path = require("node:path");
const qrcode = require("qrcode-terminal");
const { Client, LocalAuth } = require("whatsapp-web.js");

try {
  process.loadEnvFile(path.join(__dirname, "..", ".env"));
} catch {
  // No .env file: use the defaults below.
}

const VELOX_URL = (process.env.VELOX_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const PORT = Number(process.env.BRIDGE_PORT || 3001);
const TOKEN = process.env.BRIDGE_TOKEN || "";

// Message types we answer with "text only" instead of ignoring (calls, system notices and so on).
const MEDIA_TYPES = new Set(["image", "video", "audio", "ptt", "document", "sticker", "location", "vcard"]);

// Phone number ("+355...") -> WhatsApp chat id, so replies and reminders reach the same chat.
const chats = new Map();

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: path.join(__dirname, ".wwebjs_auth") }),
});

let ready = false;

client.on("qr", (qr) => {
  console.log("Scan this QR code with the spare phone: WhatsApp > Linked devices > Link a device");
  qrcode.generate(qr, { small: true });
});
client.on("ready", () => {
  ready = true;
  console.log("WhatsApp connected. Messages to this number now go to the Velox bot.");
});
client.on("auth_failure", (msg) => console.error("WhatsApp login failed:", msg));
client.on("disconnected", (reason) => {
  ready = false;
  console.error("WhatsApp disconnected:", reason, "- restart the bridge.");
});

async function phoneOf(msg) {
  const [user, server] = msg.from.split("@");
  if (server === "c.us") return "+" + user;
  // Newer chats use a hidden id ("@lid"); the contact still knows the real number.
  try {
    const contact = await msg.getContact();
    if (contact.number) return "+" + contact.number;
  } catch {}
  return msg.from;
}

async function chatIdFor(to) {
  if (to.includes("@")) return to;
  if (chats.has(to)) return chats.get(to);
  const digits = to.replace(/\D/g, "");
  const id = await client.getNumberId(digits);
  return id ? id._serialized : `${digits}@c.us`;
}

client.on("message", async (msg) => {
  if (msg.fromMe || msg.isStatus || msg.from.endsWith("@g.us") || msg.from === "status@broadcast") return;
  let text;
  if (msg.type === "chat") text = msg.body;
  else if (MEDIA_TYPES.has(msg.type)) text = "";
  else return;

  const phone = await phoneOf(msg);
  chats.set(phone, msg.from);
  console.log(`<- ${phone}: ${text || "[" + msg.type + "]"}`);

  try {
    (await msg.getChat()).sendStateTyping();
  } catch {}

  try {
    const res = await fetch(`${VELOX_URL}/bridge/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Bridge-Token": TOKEN },
      body: JSON.stringify({ from: phone, text }),
    });
    if (!res.ok) throw new Error(`bot answered HTTP ${res.status}: ${await res.text()}`);
    const { reply } = await res.json();
    if (reply && reply.trim()) {
      await client.sendMessage(msg.from, reply);
      console.log(`-> ${phone}: ${reply}`);
    }
  } catch (err) {
    console.error(`Could not answer ${phone}. Is the Python bot running at ${VELOX_URL}?`, err.message);
  }
});

// The bot calls this for owner alerts and reminders.
const server = http.createServer((req, res) => {
  const reply = (status, data) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(data));
  };
  if (req.method !== "POST" || req.url !== "/send") return reply(404, { error: "not found" });
  if (TOKEN && req.headers["x-bridge-token"] !== TOKEN) return reply(403, { error: "bad token" });
  if (!ready) return reply(503, { error: "WhatsApp is not connected yet" });

  let raw = "";
  req.on("data", (chunk) => (raw += chunk));
  req.on("end", async () => {
    try {
      const { to, body } = JSON.parse(raw);
      if (!to || !body) return reply(400, { error: "need to and body" });
      await client.sendMessage(await chatIdFor(String(to)), String(body));
      console.log(`-> ${to}: ${body}`);
      reply(200, { status: "sent" });
    } catch (err) {
      console.error("Could not send:", err.message);
      reply(500, { error: err.message });
    }
  });
});

server.listen(PORT, "127.0.0.1", () => console.log(`Bridge listening on http://127.0.0.1:${PORT}`));
client.initialize();
