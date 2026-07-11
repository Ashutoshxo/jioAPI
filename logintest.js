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

async function getAllCookies(page) {
  const client = await page.target().createCDPSession();
  const result = await client.send("Network.getAllCookies");
  await client.detach();
  return result.cookies || [];
}

function cookieKey(cookie) {
  return `${cookie.name || ""}|${cookie.domain || ""}|${cookie.path || "/"}`;
}

function isTargetCookie(cookie) {
  return /jiomart\.com|relianceretail\.com|jiomartjcp\.com/.test(String(cookie.domain || ""));
}

function normalizeCookies(cookieList) {
  const map = new Map();
  for (const cookie of cookieList || []) {
    if (!cookie || !cookie.name || !cookie.value || !isTargetCookie(cookie)) {
      continue;
    }
    const variants = [browserCookie(cookie)];
    if (["R.session", "app_location_details", "app_geolocation"].includes(cookie.name)) {
      variants.push(
        browserCookie({
          ...cookie,
          domain: ".jiomart.com",
          path: "/",
        }),
      );
    }
    for (const variant of variants) {
      map.set(cookieKey(variant), variant);
    }
  }
  return Array.from(map.values());
}

async function readPageStorage(page) {
  return page
    .evaluate(() => {
      const readStore = (store) => {
        const entries = [];
        for (let index = 0; index < store.length; index += 1) {
          const name = store.key(index);
          entries.push({ name, value: store.getItem(name) || "" });
        }
        return entries;
      };

      return {
        origin: window.location.origin,
        localStorage: readStore(window.localStorage),
        sessionStorage: readStore(window.sessionStorage),
      };
    })
    .catch(() => null);
}

async function saveBrowserStorage(email, page, existingProfile) {
  const allCookies = loadAllCookies();
  const profile = profileFromValue(allCookies[email] || existingProfile);
  const pageStorage = await readPageStorage(page);
  const cookiesNow = await getAllCookies(page);

  profile.cookies = normalizeCookies(cookiesNow);

  if (pageStorage && pageStorage.origin && pageStorage.origin !== "null") {
    const origin = siteFromOrigin(pageStorage.origin);
    const otherOrigins = (profile.origins || []).filter(
      (item) => siteFromOrigin(item.origin) !== origin,
    );
    profile.origins = [
      ...otherOrigins,
      {
        origin,
        localStorage: pageStorage.localStorage || [],
      },
    ];
    profile.sessionStorage = {
      ...(profile.sessionStorage || {}),
      [origin]: pageStorage.sessionStorage || [],
    };
  }

  allCookies[email] = profile;
  fs.writeFileSync(COOKIE_FILE, JSON.stringify(allCookies, null, 2), "utf8");
  return {
    cookies: profile.cookies.length,
    origins: profile.origins.length,
    sessionStorage: Object.values(profile.sessionStorage || {}).reduce(
      (sum, items) => sum + (Array.isArray(items) ? items.length : 0),
      0,
    ),
  };
}

async function holdBrowserIfNeeded(headless, holdMs) {
  if (headless || holdMs <= 0) {
    return;
  }
  console.log(`\nHolding browser open for ${Math.ceil(holdMs / 1000)} seconds for inspection...`);
  await new Promise((resolve) => setTimeout(resolve, holdMs));
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

  console.log("============================================================");
  console.log("JioMart Login Test");
  console.log("============================================================");
  console.log(`Account : ${email}`);
  console.log(`Browser : ${useEdge && fs.existsSync(EDGE_PATH) ? "Microsoft Edge" : "Puppeteer Chromium"}`);
  console.log(`Mode    : ${headless ? "headless" : "headed"}`);
  console.log(`Hold    : ${headless ? "0s" : `${Math.ceil(holdMs / 1000)}s`}`);
  console.log(`Cookies : ${profile.cookies.length}`);
  console.log(`Origins : ${(profile.origins || []).length}`);
  console.log("============================================================");

  let browser;
  try {
    browser = await puppeteer.launch(launchOptions);
  } catch (err) {
    if (useEdge || !fs.existsSync(EDGE_PATH)) {
      throw err;
    }
    console.log(`Bundled Chromium launch failed: ${err.message}`);
    console.log("Falling back to Microsoft Edge. Pass --edge to force this mode.");
    launchOptions.executablePath = EDGE_PATH;
    browser = await puppeteer.launch(launchOptions);
  }
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

  const validCookies = profile.cookies
    .filter((cookie) => cookie && cookie.name && cookie.value)
    .map(browserCookie);

  await page.setCookie(...validCookies);

  let interceptedAddressApi = null;
  page.on("response", async (response) => {
    const url = response.url();
    if (!url.includes("/api/service/application/cart/v1.0/address") || !url.includes("checkout_mode")) {
      return;
    }
    try {
      const text = await response.text();
      interceptedAddressApi = {
        url,
        status: response.status(),
        ok: response.ok(),
        preview: text.slice(0, 400),
      };
    } catch (err) {
      interceptedAddressApi = {
        url,
        status: response.status(),
        ok: response.ok(),
        preview: err.message,
      };
    }
  });

  await page.goto("https://www.jiomart.com/", {
    waitUntil: "domcontentloaded",
    timeout: 60000,
  });
  await new Promise((resolve) => setTimeout(resolve, 5000));

  await page.goto("https://www.jiomart.com/cart/bag", {
    waitUntil: "domcontentloaded",
    timeout: 60000,
  }).catch((err) => {
    console.log(`Cart page load warning: ${err.message}`);
  });
  await new Promise((resolve) => setTimeout(resolve, 10000));

  const cookieNames = (await getAllCookies(page)).map((cookie) => cookie.name);
  const hasSessionCookie = cookieNames.includes("R.session");
  const pageInfo = await page
    .evaluate(() => ({
      url: window.location.href,
      title: document.title,
      bodyText: document.body?.innerText?.slice(0, 1000) || "",
      localStorageCount: window.localStorage.length,
      sessionStorageCount: window.sessionStorage.length,
    }))
    .catch((err) => ({ error: err.message }));

  const rawFetchResult = await page
    .evaluate(async () => {
      const response = await fetch(
        "/api/service/application/cart/v1.0/address?checkout_mode=self",
        {
          credentials: "include",
          headers: { accept: "application/json, text/plain, */*" },
        },
      );
      const text = await response.text();
      return {
        status: response.status,
        ok: response.ok,
        preview: text.slice(0, 400),
      };
    })
    .catch((err) => ({
      status: 0,
      ok: false,
      preview: err.message,
    }));
  const apiResult = interceptedAddressApi || rawFetchResult;

  fs.mkdirSync(DEBUG_DIR, { recursive: true });
  const safeEmail = email.replace(/[^a-zA-Z0-9_]/g, "_");
  const screenshotPath = path.join(DEBUG_DIR, `logintest-${safeEmail}.png`);
  await page.screenshot({ path: screenshotPath, fullPage: true }).catch(() => {});

  console.log("\nResult");
  console.log("------------------------------------------------------------");
  console.log(`R.session cookie present : ${hasSessionCookie ? "yes" : "no"}`);
  console.log(`Address API status       : ${interceptedAddressApi ? apiResult.status : `${apiResult.status} (raw fetch fallback)`}`);
  console.log(`localStorage items       : ${pageInfo.localStorageCount ?? "?"}`);
  console.log(`sessionStorage items     : ${pageInfo.sessionStorageCount ?? "?"}`);
  console.log(`Final URL                : ${pageInfo.url || "?"}`);
  console.log(`Screenshot               : ${screenshotPath}`);

  let exitCode = 3;
  if (interceptedAddressApi && apiResult.status === 200) {
    console.log("\n✅ VALID: a.json session is currently usable for API order flow.");
    exitCode = 0;
  } else if (interceptedAddressApi && apiResult.status === 401) {
    console.log("\n❌ EXPIRED: JioMart API rejected this session with 401.");
    console.log("Fresh login/export is needed for this account.");
    exitCode = 1;
  } else {
    console.log("\n⚠️ UNKNOWN: Browser opened, but API validity could not be confirmed.");
    if (!interceptedAddressApi) {
      console.log("The cart page did not trigger the signed browser address API request.");
      console.log("Raw fetch fallback can return 401 even when cookies are valid, so do not treat this alone as expiry.");
    }
    console.log(`API preview: ${apiResult.preview}`);
  }

  await holdBrowserIfNeeded(headless, holdMs);
  const saved = await saveBrowserStorage(email, page, profile);
  console.log("\nSaved refreshed browser state to a.json");
  console.log(`Saved cookies          : ${saved.cookies}`);
  console.log(`Saved origins          : ${saved.origins}`);
  console.log(`Saved sessionStorage   : ${saved.sessionStorage}`);

  await browser.close();
  process.exit(exitCode);
}

main().catch((err) => {
  console.error(err);
  process.exit(4);
});
