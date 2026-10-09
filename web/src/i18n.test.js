import { describe, expect, it } from "vitest";
import { maskAadhaar, money, usableUrl } from "./format.js";
import { scriptLang, STRINGS, tr } from "./i18n.js";

const placeholders = (s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

describe("UI strings", () => {
  it("kn and hi have every English key, with the same placeholders", () => {
    for (const lang of ["kn", "hi"]) {
      expect(Object.keys(STRINGS[lang]).sort()).toEqual(Object.keys(STRINGS.en).sort());
      for (const [k, v] of Object.entries(STRINGS.en)) {
        expect([lang, k, placeholders(STRINGS[lang][k])]).toEqual([lang, k, placeholders(v)]);
      }
    }
  });

  it("fills placeholders and falls back to English", () => {
    expect(tr("en", "docs_progress", { ready: 2, total: 5 })).toBe("2 of 5 ready");
    expect(tr("xx", "send")).toBe("Send");
  });

  it("the yes words are ones the agent's gate accepts", () => {
    expect([STRINGS.kn.yes_word, STRINGS.hi.yes_word, STRINGS.en.yes_word]).toEqual(["ಹೌದು", "हाँ", "yes"]);
  });

  it("detects the script of typed text", () => {
    expect(scriptLang("ನನಗೆ 62 ವರ್ಷ")).toBe("kn");
    expect(scriptLang("मैं 62 साल का हूँ")).toBe("hi");
    expect(scriptLang("I'm 62")).toBeNull();
  });
});

describe("formatting", () => {
  it("money in Indian grouping, like the agent", () => {
    expect(money(120000)).toBe("₹1,20,000");
    expect(money(300000)).toBe("₹3,00,000");
  });

  it("masks 12-digit runs as Aadhaar, leaves phone numbers alone", () => {
    expect(maskAadhaar("aadhaar 2345-6789-0123")).toBe("aadhaar XXXX XXXX 0123");
    expect(maskAadhaar("+919845012345")).toBe("+919845012345");
    expect(maskAadhaar("9845012345")).toBe("9845012345");
  });

  it("does not link the placeholder portal URL", () => {
    expect(usableUrl("https://<ayush-portal-public-url>/schemes/pension-001")).toBeNull();
    expect(usableUrl("{MOCK_PORTAL_URL}/schemes/pension-001")).toBeNull();
    expect(usableUrl("https://portal.example.org/schemes/pension-001")).toBe("https://portal.example.org/schemes/pension-001");
  });
});
