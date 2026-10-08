# YojanaSaathi: project brief

Background and decisions carried over from planning. `CLAUDE.md` holds the rules;
this file holds the why, the scope and the demo.

## Problem
Eligible citizens miss scholarships, pensions and subsidies because the path from
"Am I eligible?" to "Submitted" breaks at four points:
1. Scattered rules: 5,000+ schemes, eligibility buried in jargon
2. Document confusion: unclear, person-specific lists -> incomplete or rejected applications
3. Complex portals: English-first forms and OTP steps push people to paid middlemen
4. No follow-up: status, deadlines and pending actions left to the citizen

Evidence used in the pitch: 5,000+ schemes on myScheme; 800M+ citizens eligible for
welfare while much of a ~$210B/yr budget goes unused (World Bank figures via
Haqdarshak / MIT Solve); only ~38% of Indians 15+ can do an online banking
transaction while 94% of rural homes own a phone (NSSO CAMS 2022-23). That last
point is why the product is voice-first and also reachable by phone call.

Target users: senior citizens, students, farmers, low-income households and
first-time digital users in Karnataka; CSC operators and NGO workers as helpers.

Existing gap: myScheme and chatbots stop at discovery; assisted models need human
field agents; research covers voice, RPA and form filling separately. Nobody runs
the whole loop with a human approval gate and persistent case memory.

## The loop (7 steps)
Interview -> Eligibility (with reasons + cited source) -> Checklist -> Plan ->
Pre-fill (browser agent) -> **You confirm** -> Track (status + reminders), then repeat.
Pain point -> fix: scattered rules -> eligibility; document confusion -> checklist;
complex portals -> pre-fill; no follow-up -> track.

## MVP features
Must-have:
1. Voice + text conversation in kn / hi / en (Pipecat + Sarvam), barge-in, text fallback
2. Smart interview: extracts facts, asks only fields the remaining schemes need
3. Consented, reusable citizen profile
4. Eligibility engine: JSON Logic for 3-5 schemes, reason + official source + date
5. Personal document checklist with missing items flagged
6. Encrypted document upload (AES-256), Aadhaar masked, auto-delete after submission
7. Browser pre-fill (Playwright) on the mock portal with step screenshots
8. OTP handoff: pause, citizen supplies code, continue
9. Human confirmation gate: review screen, spoken read-back, edits, explicit yes
10. Submission + case memory: app ID stored, resume across sessions
11. Status tracking + follow-up: scheduler polls, push notification + spoken update
12. Safe-stop when the portal changes
13. Audit log of every consequential action
14. **Phone access: dial a number, talk to the same agent** (added after round 1)

Should-have: evaluation script vs manual baseline; status timeline view;
LangGraph Studio open during Q&A.
Stretch: a scholarship scheme for a Hindi student demo; mocked DigiLocker fetch.

## Schemes
Pick 3-5 with different rule shapes. Mock portal currently seeds demo schemes,
including `pension-001` "Senior Citizen Pension Scheme" (age >= 60, income <= 3,00,000)
which drives the golden-path demo. Candidates for real-world modelling: an old-age
pension, an NSP scholarship, PM-KISAN, a Karnataka scheme such as Gruha Lakshmi.
Encode rules only from official pages, with URL and effective date.

Rule file shape (`rules/<scheme_id>.json`):
```json
{
  "scheme_id": "pension-001",
  "title": "Senior Citizen Pension Scheme",
  "rule": {"and": [{">=": [{"var": "age"}, 60]}, {"<=": [{"var": "annual_income"}, 300000]}]},
  "required_fields": ["age", "annual_income"],
  "documents": [{"doc": "aadhaar", "when": null}, {"doc": "income_certificate", "when": null}],
  "source_url": "<official page>",
  "effective_date": "2026-04-01"
}
```

## UI (design canvas "YojanaSaathi UI")
Palette: deep green #0B5D4B, marigold #F2B21B, ink #17201C, panel #F4F2EC,
amber warning #FFF3CF / #7A4A00. Fonts: Bricolage Grotesque (display),
Hanken Grotesk (body), Noto Sans Kannada / Devanagari. Large text, 44px+ targets,
Kannada first with English underneath.

Mobile screens:
1. Talk: big mic button with waveform, "What I know so far" profile chips,
   "Why I ask" note under each question, replay, type-instead, language switch,
   card with the phone number for people without smartphones
2. Schemes for you: states "You qualify" / "1 question left" / "Don't qualify, see why";
   WHY box (rule vs user value), official rule link + effective date, read aloud
3. Documents: progress (4 of 5), missing item pinned with "Take photo",
   Aadhaar masked, encryption + auto-delete note, "Delete now"
4. Pre-fill + OTP: live step list of the browser agent, "Your turn: enter the OTP"
   (type or say), "never reads your messages", pause and finish later
5. Review & confirm: read-aloud progress, edit per field, low-confidence field in amber,
   form screenshots, "ಹೌದು, ಸಲ್ಲಿಸಿ · Yes, submit", "Nothing is submitted without your yes"
6. My applications: Kannada push notification, status timeline, "What to do" box with
   upload + listen, "Checked 5 min ago", next eligible scheme
Website: language toggle, Start talking + Call buttons, how it works (4 steps),
call band, trust points, helper mode for CSC/NGO workers, "Not a government website".

## Phone access
Twilio number -> Pipecat telephony transport -> Sarvam STT/TTS -> same `/turn`.
Caller number = case lookup, so a citizen can start on a call and finish in the app.
No "press 1" menus; language detected from speech. Confirm by voice ("ಹೌದು").
Documents can't be uploaded by voice: send an upload link (or visit a CSC).
Indian numbers need KYC and take days; use a Twilio trial number for the demo.
SMS in India needs DLT registration: simulate it or read the app ID aloud.

## Golden-path demo
1. Speak Kannada: "ನನಗೆ 62 ವರ್ಷ. ನನಗೆ ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?" (I'm 62. Can I get a pension?)
2. Agent asks 1-2 missing questions (income), shows eligible scheme with cited rule
3. Checklist with one missing document
4. "Proceed": Playwright fills the mock portal live (split screen)
5. OTP pause: teammate reads the code from the portal's demo inbox on a second phone
6. Review + read-back; answer "ಹೌದು"; application ID appears
7. Teammate flips status in Django admin -> push notification in Kannada
Optional: toggle a renamed field on the portal to show safe-stop.
Backups: recorded video; text mode in the same UI if voice fails.

## Evaluation
Eligibility accuracy, evidence correctness, checklist accuracy, form-field accuracy,
human correction rate at review, time to prepared application, follow-up correctness;
compare against manual application on 30-50 labelled profiles.

## Answers to likely judge questions
- Hallucination: LLM never decides eligibility; rules decide, source is cited.
- Portal legality/fragility: sandbox replica; production = allow-listed portals; safe-stop.
- OTP/CAPTCHA: always the citizen; never bypassed.
- Documents: AES-256, metadata only in DB, Aadhaar masked, auto-delete, audit log;
  production path = DigiLocker fetch with consent.
- Scale: new scheme = rules file + document map + portal script; graph unchanged.
- No smartphone: dial-in phone line runs the same agent.
