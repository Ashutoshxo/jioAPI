const fs = require("fs");
const path = require("path");
const readline = require("readline");
const puppeteer = require("puppeteer");

const DIR = __dirname;
const COOKIE_FILE = path.join(DIR, "a.json");
const EDGE_PATH = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const DEBUG_DIR = path.join(DIR, "debug");

function getActiveEmail() {
  const idx = process.argv.indexOf("--email");
  if (idx >= 0 && process.argv[idx + 1]) {
    return process.argv[idx + 1];
  }

  const credsFile = path.join(DIR, "credentials.json");
  if (fs.existsSync(credsFile)) {
    try {
      const creds = JSON.parse(fs.readFileSync(credsFile, "utf8"));
      if (creds.email && creds.email !== "your_email@example.com") {
        return creds.email;
      }
    } catch (_) {}
  }

  return "default";
}

function getUserDataPath(email) {
  const safeName = email.replace(/[^a-zA-Z0-9_]/g, "_");
  return path.join(DIR, "user_data", safeName);
}

function loadAllCookies() {
  if (!fs.existsSync(COOKIE_FILE)) {
    return {};
  }

  try {
    const raw = fs.readFileSync(COOKIE_FILE, "utf8").trim();
    if (!raw) {
      return {};
    }
    const data = JSON.parse(raw);
    return Array.isArray(data) ? { [getActiveEmail()]: data } : data;
  } catch (err) {
    console.error(`Could not parse a.json: ${err.message}`);
    return {};
  }
}

function cookieKey(cookie) {
  return `${cookie.name || ""}|${cookie.domain || ""}|${cookie.path || "/"}`;
}

function isJioMartCookie(cookie) {
  return String(cookie.domain || "").includes("jiomart.com");
}

function normalizeCookies(cookieList) {
  const map = new Map();

  for (const cookie of cookieList || []) {
    if (!cookie || !cookie.name || !cookie.value) {
      continue;
    }

    const variants = [{ ...cookie }];
    if (
      isJioMartCookie(cookie) &&
      ["R.session", "app_location_details", "app_geolocation"].includes(cookie.name)
    ) {
      variants.push({
        ...cookie,
        domain: ".jiomart.com",
        path: "/",
      });
    }

    for (const variant of variants) {
      map.set(cookieKey(variant), variant);
    }
  }

  return Array.from(map.values());
}

function saveCookies(email, cookieList) {
  const allCookies = loadAllCookies();
  allCookies[email] = normalizeCookies(cookieList);
  fs.writeFileSync(COOKIE_FILE, JSON.stringify(allCookies, null, 2), "utf8");
}

function waitForEnter(prompt) {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  return new Promise((resolve) => {
    rl.question(prompt, () => {
      rl.close();
      resolve();
    });
  });
}

function hasArg(name) {
  return process.argv.includes(name);
}

function ensureDebugDir() {
  if (!fs.existsSync(DEBUG_DIR)) {
    fs.mkdirSync(DEBUG_DIR, { recursive: true });
  }
}

async function applyBrowserPatches(page) {
  await page.evaluateOnNewDocument(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
    Object.defineProperty(navigator, "plugins", { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, "languages", { get: () => ["en-US", "en"] });
    window.chrome = window.chrome || { runtime: {} };
  });
}

function attachDiagnostics(page, events) {
  page.on("console", (message) => {
    events.console.push({
      type: message.type(),
      text: message.text(),
    });
  });

  page.on("pageerror", (error) => {
    events.pageErrors.push(String(error.stack || error.message || error));
  });

  page.on("requestfailed", (request) => {
    events.requestFailures.push({
      url: request.url(),
      method: request.method(),
      failure: request.failure()?.errorText || "unknown",
      resourceType: request.resourceType(),
    });
  });

  page.on("response", (response) => {
    const status = response.status();
    if (status >= 400) {
      events.badResponses.push({
        url: response.url(),
        status,
        resourceType: response.request().resourceType(),
      });
    }
  });
}

async function getAllCookies(page) {
  const client = await page.target().createCDPSession();
  const result = await client.send("Network.getAllCookies");
  await client.detach();
  return result.cookies || [];
}

async function clickSignInIfVisible(page) {
  const selectors = [
    'a[href="/profile"]',
    'a[href*="/profile"]',
    "a[href*='login']",
    "#sign-in",
    ".sign-in",
    ".login-icon-content",
    "a.logged-user-name",
  ];

  for (const selector of selectors) {
    const handle = await page.$(selector);
    if (!handle) {
      continue;
    }
    try {
      await handle.click();
      console.log("Clicked Sign In/login element.");
      return true;
    } catch (_) {}
  }

  const clickedByText = await page.evaluate(() => {
    const nodes = Array.from(document.querySelectorAll("a, button, div, span"));
    const node = nodes.find((el) => /sign\s*in/i.test(el.textContent || ""));
    if (node) {
      node.click();
      return true;
    }
    return false;
  });

  if (clickedByText) {
    console.log("Clicked Sign In by text.");
  }
  return clickedByText;
}

async function runDebug(browser, page) {
  ensureDebugDir();

  const events = {
    console: [],
    pageErrors: [],
    requestFailures: [],
    badResponses: [],
  };
  attachDiagnostics(page, events);

  console.log("DEBUG: opening JioMart homepage...");
  let navigationError = null;
  try {
    await page.goto("https://www.jiomart.com/", {
      waitUntil: "domcontentloaded",
      timeout: 60000,
    });
  } catch (err) {
    navigationError = err.message;
    console.log(`DEBUG: homepage load error: ${err.message}`);
  }

  await new Promise((resolve) => setTimeout(resolve, 8000));

  console.log(`DEBUG: current URL after homepage load: ${page.url()}`);
  const clicked = await clickSignInIfVisible(page);
  console.log(`DEBUG: clicked sign-in: ${clicked ? "yes" : "no"}`);

  await new Promise((resolve) => setTimeout(resolve, 12000));

  const cookiesNow = await getAllCookies(page);
  const html = await page.content().catch((err) => `HTML capture failed: ${err.message}`);
  const title = await page.title().catch(() => "");
  const bodyText = await page.evaluate(() => document.body?.innerText || "").catch(() => "");
  const screenshotPath = path.join(DEBUG_DIR, "auth-debug.png");
  const htmlPath = path.join(DEBUG_DIR, "auth-debug.html");
  const jsonPath = path.join(DEBUG_DIR, "auth-debug.json");

  await page.screenshot({ path: screenshotPath, fullPage: true }).catch((err) => {
    events.pageErrors.push(`screenshot failed: ${err.message}`);
  });
  fs.writeFileSync(htmlPath, html, "utf8");
  fs.writeFileSync(
    jsonPath,
    JSON.stringify(
      {
        navigationError,
        finalUrl: page.url(),
        title,
        bodyTextLength: bodyText.length,
        bodyTextPreview: bodyText.slice(0, 500),
        htmlLength: html.length,
        cookieNames: cookiesNow.map((cookie) => ({
          name: cookie.name,
          domain: cookie.domain,
          path: cookie.path,
          expires: cookie.expires,
        })),
        events,
      },
      null,
      2,
    ),
    "utf8",
  );

  console.log(`DEBUG: saved screenshot: ${screenshotPath}`);
  console.log(`DEBUG: saved html: ${htmlPath}`);
  console.log(`DEBUG: saved report: ${jsonPath}`);
  console.log(`DEBUG: final URL: ${page.url()}`);
  console.log(`DEBUG: title: ${title}`);
  console.log(`DEBUG: body text length: ${bodyText.length}`);
  console.log(`DEBUG: page errors: ${events.pageErrors.length}`);
  console.log(`DEBUG: failed requests: ${events.requestFailures.length}`);
  console.log(`DEBUG: bad responses: ${events.badResponses.length}`);

  await browser.close();
}

async function main() {
  const email = getActiveEmail();
  const userDataDir = getUserDataPath(email);

  console.log(`Starting Puppeteer cookie capture for ${email}...`);
  console.log(`Using persistent profile directory: ${userDataDir}`);

  if (!fs.existsSync(EDGE_PATH)) {
    throw new Error(`Edge executable not found: ${EDGE_PATH}`);
  }

  const browser = await puppeteer.launch({
    executablePath: EDGE_PATH,
    userDataDir,
    headless: false,
    defaultViewport: null,
    ignoreDefaultArgs: ["--enable-automation", "--no-sandbox"],
    args: [
      "--start-maximized",
      "--disable-blink-features=AutomationControlled",
      "--no-first-run",
      "--no-default-browser-check",
    ],
  });

  const pages = await browser.pages();
  const page = pages[0] || (await browser.newPage());
  await applyBrowserPatches(page);

  if (hasArg("--debug")) {
    await runDebug(browser, page);
    return;
  }

  console.log("Opening JioMart homepage...");
  try {
    await page.goto("https://www.jiomart.com/", {
      waitUntil: "domcontentloaded",
      timeout: 60000,
    });
  } catch (err) {
    console.log(`Initial page load did not finish cleanly: ${err.message}`);
  }

  await new Promise((resolve) => setTimeout(resolve, 5000));
  const beforeCookies = await getAllCookies(page);
  const hasSessionBefore = beforeCookies.some((cookie) => cookie.name === "R.session");

  if (hasSessionBefore) {
    console.log("R.session already exists in this browser profile.");
  } else {
    console.log("R.session not found yet. Opening login manually if possible...");
    await clickSignInIfVisible(page);
  }

  console.log("\n============================================================");
  console.log("INSTRUCTIONS:");
  console.log("1. If page is blank, type https://www.jiomart.com/ in this browser and press Enter.");
  console.log("2. Sign in and complete OTP in this same automated Edge window.");
  console.log("3. After login is complete, return here and press ENTER.");
  console.log("============================================================\n");

  await waitForEnter("Press Enter here AFTER successful login...");

  const savedCookies = await getAllCookies(page);
  saveCookies(email, savedCookies);

  const sessionCookie = savedCookies.find((cookie) => cookie.name === "R.session");
  console.log(`Saved ${savedCookies.length} cookies to a.json for ${email}.`);
  console.log(`R.session present: ${sessionCookie ? "yes" : "no"}`);
  if (sessionCookie) {
    console.log(`R.session domain: ${sessionCookie.domain || "(none)"}`);
    console.log(`R.session path: ${sessionCookie.path || "/"}`);
    console.log(`R.session expires: ${sessionCookie.expires || "session/server-controlled"}`);
  }

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
