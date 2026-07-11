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

function loadAllProfiles() {
  if (!fs.existsSync(COOKIE_FILE)) {
    return {};
  }
  const raw = fs.readFileSync(COOKIE_FILE, "utf8").trim();
  if (!raw) {
    return {};
  }
  const data = JSON.parse(raw);
  return Array.isArray(data) ? { default: { cookies: data } } : data;
}

function emptyProfile() {
  return {
    cookies: [],
    origins: [],
    sessionStorage: {},
  };
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

function looksRelevant(url, body, orderId) {
  const lowerUrl = url.toLowerCase();
  const lowerBody = (body || "").toLowerCase();
  return (
    lowerUrl.includes("order") ||
    lowerUrl.includes("shipment") ||
    lowerUrl.includes("delivery") ||
    lowerUrl.includes("tracking") ||
    lowerBody.includes("otp") ||
    lowerBody.includes("delivery otp") ||
    (orderId && (body || "").includes(orderId))
  );
}

function extractOtpCandidates(text) {
  const hits = [];
  const source = text || "";
  const patterns = [
    /(?:otp|pin|code)[^0-9]{0,40}([0-9]{4,8})/gi,
    /([0-9]{4,8})[^a-z0-9]{0,20}(?:otp|pin|code)/gi,
  ];
  for (const pattern of patterns) {
    let match;
    while ((match = pattern.exec(source))) {
      hits.push(match[1]);
    }
  }
  return [...new Set(hits)];
}

async function main() {
  const email = getArgValue("--email", process.env.JIOMART_ACCOUNT || "default");
  const orderId = getArgValue("--order-id", "");
  const targetUrl = getArgValue("--url", "https://www.jiomart.com/profile/orders");
  const headed = hasArg("--headed");
  const holdMs = Number(getArgValue("--hold-ms", "8000"));
  const profiles = loadAllProfiles();
  const profile = profileFromValue(profiles[email]);

  if (!profile.cookies.length) {
    console.error(`No cookies found in a.json for account: ${email}`);
    process.exit(1);
  }

  fs.mkdirSync(DEBUG_DIR, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const capturePath = path.join(DEBUG_DIR, `orders_otp_capture_${email}_${stamp}.json`);
  const screenshotPath = path.join(DEBUG_DIR, `orders_otp_capture_${email}_${stamp}.png`);

  const launchOptions = {
    headless: !headed,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  };
  if (fs.existsSync(EDGE_PATH)) {
    launchOptions.executablePath = EDGE_PATH;
  }

  const browser = await puppeteer.launch(launchOptions);
  const page = await browser.newPage();
  const responses = [];

  page.on("response", async (response) => {
    const url = response.url();
    if (!url.includes("jiomart.com") && !url.includes("jiomartjcp.com")) {
      return;
    }
    const headers = response.headers();
    const contentType = headers["content-type"] || "";
    if (!contentType.includes("json") && !contentType.includes("text")) {
      return;
    }
    try {
      const body = await response.text();
      if (!looksRelevant(url, body, orderId)) {
        return;
      }
      responses.push({
        url,
        status: response.status(),
        contentType,
        otp_candidates: extractOtpCandidates(body),
        body: body.slice(0, 12000),
      });
    } catch (_) {
      // Some browser responses cannot be read after redirects/cache.
    }
  });

  const storage = storagePayload(profile);
  for (const [origin, current] of Object.entries(storage)) {
    await page.goto(origin, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.evaluate((items) => {
      for (const item of items.localStorage || []) {
        window.localStorage.setItem(item.name, item.value || "");
      }
      for (const item of items.sessionStorage || []) {
        window.sessionStorage.setItem(item.name, item.value || "");
      }
    }, current);
  }

  await page.setCookie(...profile.cookies.filter((cookie) => cookie.name).map(browserCookie));
  await page.goto(targetUrl, {
    waitUntil: "networkidle2",
    timeout: 90000,
  });

  if (orderId && targetUrl.endsWith("/profile/orders")) {
    const clicked = await page.evaluate((targetOrderId) => {
      const elements = Array.from(document.querySelectorAll("a,button,div,span"));
      const match = elements.find((el) => (el.innerText || "").includes(targetOrderId));
      if (match) {
        match.click();
        return true;
      }
      return false;
    }, orderId);
    if (clicked) {
      await new Promise((resolve) => setTimeout(resolve, 5000));
    }
  }

  await new Promise((resolve) => setTimeout(resolve, holdMs));

  const pageText = await page.evaluate(() => document.body.innerText || "");
  const pageUrl = page.url();
  const pageOtpCandidates = extractOtpCandidates(pageText);
  await page.screenshot({ path: screenshotPath, fullPage: true });

  const result = {
    account: email,
    order_id: orderId,
    page_url: pageUrl,
    page_otp_candidates: pageOtpCandidates,
    response_count: responses.length,
    responses,
    screenshot: screenshotPath,
    captured_at: new Date().toISOString(),
  };

  fs.writeFileSync(capturePath, JSON.stringify(result, null, 2), "utf8");
  await browser.close();

  console.log(`Saved capture: ${capturePath}`);
  console.log(`Saved screenshot: ${screenshotPath}`);
  if (pageOtpCandidates.length) {
    console.log(`Page OTP candidates: ${pageOtpCandidates.join(", ")}`);
  }
  const responseOtps = [...new Set(responses.flatMap((item) => item.otp_candidates || []))];
  if (responseOtps.length) {
    console.log(`Response OTP candidates: ${responseOtps.join(", ")}`);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
