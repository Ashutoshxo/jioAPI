const fs = require("fs");
const http = require("http");
const path = require("path");
const readline = require("readline");
const { spawn } = require("child_process");
const puppeteer = require("puppeteer");

const DIR = __dirname;
const COOKIE_FILE = path.join(DIR, "a.json");
const EDGE_PATH = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const DEBUG_PORT = Number(process.env.JIOMART_EDGE_DEBUG_PORT || 9222);

function getActiveEmail() {
  const idx = process.argv.indexOf("--email");
  if (idx >= 0 && process.argv[idx + 1]) {
    return process.argv[idx + 1];
  }
  return "default";
}

function getArgValue(name, fallback = null) {
  const idx = process.argv.indexOf(name);
  if (idx >= 0 && process.argv[idx + 1]) {
    return process.argv[idx + 1];
  }
  return fallback;
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
  return /jiomart\.com|relianceretail\.com|jiomartjcp\.com/.test(String(cookie.domain || ""));
}

function normalizeCookies(cookieList) {
  const map = new Map();

  for (const cookie of cookieList || []) {
    if (!cookie || !cookie.name || !cookie.value || !isJioMartCookie(cookie)) {
      continue;
    }

    const variants = [{ ...cookie }];
    if (["R.session", "app_location_details", "app_geolocation"].includes(cookie.name)) {
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

function requestJson(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        body += chunk;
      });
      res.on("end", () => {
        try {
          resolve(JSON.parse(body));
        } catch (err) {
          reject(err);
        }
      });
    });
    req.on("error", reject);
    req.setTimeout(1000, () => {
      req.destroy(new Error("timeout"));
    });
  });
}

async function waitForDebugEndpoint() {
  const url = `http://127.0.0.1:${DEBUG_PORT}/json/version`;
  const started = Date.now();
  while (Date.now() - started < 20000) {
    try {
      return await requestJson(url);
    } catch (_) {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  throw new Error(`Could not connect to Edge DevTools at ${url}`);
}

async function getAllCookies(page) {
  const client = await page.target().createCDPSession();
  const result = await client.send("Network.getAllCookies");
  await client.detach();
  return result.cookies || [];
}

async function main() {
  const email = getActiveEmail();
  const edgeProfile = getArgValue("--profile", "Default");
  if (!fs.existsSync(EDGE_PATH)) {
    throw new Error(`Edge executable not found: ${EDGE_PATH}`);
  }

  console.log("Starting normal Microsoft Edge with temporary DevTools export port...");
  console.log("Close all Edge windows first if this does not connect.");
  console.log(`Edge profile: ${edgeProfile}`);
  console.log(`a.json account key: ${email}`);

  const edge = spawn(
    EDGE_PATH,
    [
      `--remote-debugging-port=${DEBUG_PORT}`,
      `--profile-directory=${edgeProfile}`,
      "--new-window",
      "https://www.jiomart.com/",
    ],
    {
      detached: true,
      stdio: "ignore",
    },
  );
  edge.unref();

  const version = await waitForDebugEndpoint();
  const browser = await puppeteer.connect({
    browserURL: `http://127.0.0.1:${DEBUG_PORT}`,
    defaultViewport: null,
  });

  console.log(`Connected to: ${version.Browser || "Edge"}`);
  console.log("\n============================================================");
  console.log("INSTRUCTIONS:");
  console.log("1. Use the opened NORMAL Edge window, not an automated blank window.");
  console.log(`2. Confirm it is the right Edge profile/account: ${edgeProfile}.`);
  console.log("3. Sign in and complete OTP if needed.");
  console.log("4. After JioMart shows you logged in, return here and press ENTER.");
  console.log("============================================================\n");

  await waitForEnter("Press Enter here AFTER JioMart login is complete...");

  const pages = await browser.pages();
  const page =
    pages.find((p) => /jiomart\.com|relianceretail\.com/.test(p.url())) ||
    pages[0] ||
    (await browser.newPage());

  const allCookies = await getAllCookies(page);
  const targetCookies = normalizeCookies(allCookies);
  saveCookies(email, targetCookies);

  const sessionCookie = targetCookies.find((cookie) => cookie.name === "R.session");
  const names = Array.from(new Set(targetCookies.map((cookie) => cookie.name))).sort();

  console.log(`Saved ${targetCookies.length} JioMart/Reliance cookies to a.json profile: ${email}`);
  console.log(`Cookie names: ${names.join(", ")}`);
  console.log(`R.session present: ${sessionCookie ? "yes" : "no"}`);
  if (sessionCookie) {
    console.log(`R.session domain: ${sessionCookie.domain || "(none)"}`);
    console.log(`R.session path: ${sessionCookie.path || "/"}`);
    console.log(`R.session expires: ${sessionCookie.expires || "session/server-controlled"}`);
  }

  await browser.disconnect();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
