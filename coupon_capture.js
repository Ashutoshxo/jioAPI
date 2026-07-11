const fs = require("fs");
const path = require("path");
const puppeteer = require("puppeteer");

const DIR = __dirname;
const COOKIE_FILE = path.join(DIR, "a.json");
const DEBUG_DIR = path.join(DIR, "debug");
const EDGE_PATH = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

function getArgValue(name, fallback = null) {
  const idx = process.argv.indexOf(name);
  if (idx >= 0 && process.argv[idx + 1]) {
    return process.argv[idx + 1];
  }
  return fallback;
}

function hasArg(name) {
  return process.argv.includes(name);
}

function getActiveEmail() {
  return getArgValue("--email", process.env.JIOMART_ACCOUNT || "default");
}

function loadAllCookies() {
  if (!fs.existsSync(COOKIE_FILE)) {
    return {};
  }
  const raw = fs.readFileSync(COOKIE_FILE, "utf8").trim();
  if (!raw) {
    return {};
  }
  const data = JSON.parse(raw);
  return Array.isArray(data) ? { [getActiveEmail()]: data } : data;
}

function emptyProfile() {
  return { cookies: [], origins: [], sessionStorage: {} };
}

function profileFromValue(value) {
  if (Array.isArray(value)) {
    return { ...emptyProfile(), cookies: value };
  }
  if (value && typeof value === "object") {
    return {
      ...emptyProfile(),
      cookies: Array.isArray(value.cookies) ? value.cookies : [],
      origins: Array.isArray(value.origins) ? value.origins : [],
      sessionStorage:
        value.sessionStorage && typeof value.sessionStorage === "object"
          ? value.sessionStorage
          : {},
    };
  }
  return emptyProfile();
}

function siteFromOrigin(origin) {
  try {
    const url = new URL(origin);
    return `${url.protocol}//${url.host}`;
  } catch (_) {
    return origin;
  }
}

function browserCookie(cookie) {
  const clean = {
    name: cookie.name,
    value: cookie.value || "",
    domain: cookie.domain || ".jiomart.com",
    path: cookie.path || "/",
  };
  if (typeof cookie.expires === "number" && cookie.expires > 0) {
    clean.expires = cookie.expires;
  }
  if (typeof cookie.httpOnly === "boolean") {
    clean.httpOnly = cookie.httpOnly;
  }
  if (typeof cookie.secure === "boolean") {
    clean.secure = cookie.secure;
  }
  if (cookie.sameSite && ["Strict", "Lax", "None"].includes(cookie.sameSite)) {
    clean.sameSite = cookie.sameSite;
  }
  return clean;
}

function storagePayload(profile) {
  const payload = {};
  for (const originEntry of profile.origins || []) {
    if (!originEntry.origin) {
      continue;
    }
    const origin = siteFromOrigin(originEntry.origin);
    payload[origin] = payload[origin] || { localStorage: [], sessionStorage: [] };
    payload[origin].localStorage = originEntry.localStorage || [];
  }
  for (const [originKey, items] of Object.entries(profile.sessionStorage || {})) {
    if (!Array.isArray(items)) {
      continue;
    }
    const origin = siteFromOrigin(originKey);
    payload[origin] = payload[origin] || { localStorage: [], sessionStorage: [] };
    payload[origin].sessionStorage = items;
  }
  return payload;
}

function shouldCapture(url) {
  return /coupon|promo|offer|discount|voucher|cart|shipmentfee/i.test(url);
}

async function clickCouponEntry(page) {
  const clicked = await page
    .evaluate(() => {
      const candidates = Array.from(document.querySelectorAll("button, a, div, span"));
      const target = candidates.find((node) => {
        const text = (node.innerText || node.textContent || "").trim().toLowerCase();
        return (
          text.includes("view all offers") ||
          text.includes("coupon") ||
          text.includes("offers")
        );
      });
      if (!target) {
        return null;
      }
      target.scrollIntoView({ block: "center", inline: "center" });
      target.click();
      return (target.innerText || target.textContent || "").trim().slice(0, 120);
    })
    .catch(() => null);
  return clicked;
}

async function main() {
  const email = getActiveEmail();
  const allCookies = loadAllCookies();
  const profile = profileFromValue(allCookies[email]);
  const headless = !hasArg("--headed");
  const useEdge = hasArg("--edge");
  const holdMs = Number(getArgValue("--hold-ms", headless ? "0" : "30000"));

  if (!profile.cookies.length) {
    console.error(`No cookies found in a.json for account: ${email}`);
    process.exit(2);
  }

  const launchOptions = {
    headless,
    protocolTimeout: 120000,
    defaultViewport: { width: 1365, height: 768 },
    args: [
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-blink-features=AutomationControlled",
    ],
  };
  if (useEdge && fs.existsSync(EDGE_PATH)) {
    launchOptions.executablePath = EDGE_PATH;
  }

  const browser = await puppeteer.launch(launchOptions);
  const page = await browser.newPage();
  await page.setUserAgent(
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
  );

  const payload = storagePayload(profile);
  await page.evaluateOnNewDocument((state) => {
    const current = state[window.location.origin];
    if (!current) {
      return;
    }
    for (const item of current.localStorage || []) {
      if (item && typeof item.name === "string") {
        window.localStorage.setItem(item.name, item.value || "");
      }
    }
    for (const item of current.sessionStorage || []) {
      if (item && typeof item.name === "string") {
        window.sessionStorage.setItem(item.name, item.value || "");
      }
    }
  }, payload);

  await page.setCookie(...profile.cookies.filter((cookie) => cookie.name && cookie.value).map(browserCookie));

  const captured = [];
  page.on("request", (request) => {
    const url = request.url();
    if (!shouldCapture(url)) {
      return;
    }
    captured.push({
      type: "REQUEST",
      method: request.method(),
      url,
      headers: request.headers(),
      post_data: request.postData(),
    });
  });
  page.on("response", async (response) => {
    const url = response.url();
    if (!shouldCapture(url)) {
      return;
    }
    let body = null;
    try {
      body = await response.text();
      if (body && body.length > 20000) {
        body = body.slice(0, 20000);
      }
    } catch (err) {
      body = err.message;
    }
    captured.push({
      type: "RESPONSE",
      status: response.status(),
      url,
      body,
    });
  });

  await page.goto("https://www.jiomart.com/cart/bag", {
    waitUntil: "domcontentloaded",
    timeout: 60000,
  });
  await new Promise((resolve) => setTimeout(resolve, 8000));
  const clickedText = await clickCouponEntry(page);
  await new Promise((resolve) => setTimeout(resolve, 12000));

  fs.mkdirSync(DEBUG_DIR, { recursive: true });
  const safeEmail = email.replace(/[^a-zA-Z0-9_]/g, "_");
  const logPath = path.join(DEBUG_DIR, `coupon-network-${safeEmail}.json`);
  const screenshotPath = path.join(DEBUG_DIR, `coupon-capture-${safeEmail}.png`);
  fs.writeFileSync(logPath, JSON.stringify(captured, null, 2), "utf8");
  await page.screenshot({ path: screenshotPath, fullPage: true }).catch(() => {});

  console.log("Coupon capture complete");
  console.log(`Account       : ${email}`);
  console.log(`Clicked       : ${clickedText || "not found"}`);
  console.log(`Captured calls: ${captured.length}`);
  console.log(`Log           : ${logPath}`);
  console.log(`Screenshot    : ${screenshotPath}`);

  if (!headless && holdMs > 0) {
    console.log(`Holding browser for ${Math.ceil(holdMs / 1000)} seconds...`);
    await new Promise((resolve) => setTimeout(resolve, holdMs));
  }

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
