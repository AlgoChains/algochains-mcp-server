# AlgoChains — Regulatory Compliance Audit & Defense Memo (v1.0)

> **Date:** 2026-06-13 · **Prepared by:** AlgoChains engineering (with researched legal precedent)
> **Status:** Internal working memo — **NOT legal advice.** Engage a licensed
> commodities/securities attorney before relying on any position below. Several
> primary sources (eCFR, NFA rulebook, Cornell LII) blocked automated retrieval;
> verbatim statutory/case text must be confirmed against the official source.

This memo (a) states AlgoChains's regulatory position, (b) marshals the
precedent that supports the "software tool provider, not a CTA" defense, (c)
identifies the single feature that most threatens that defense, and (d) maps
each conclusion to concrete product/engineering controls already in or being
added to this codebase.

---

## 0. Executive summary

| | |
|---|---|
| **Strongest defense** | *Lowe v. SEC* + *Taucher v. Born* + **CFTC Reg. 4.14(a)(9)** — impersonal, identical-to-all, **subscriber-initiated** signals delivered as published analysis are protected speech and exempt from CTA registration. |
| **Biggest liability** | **Auto-execution / copy-trade that directs trades into a specific subscriber's account** (*CFTC v. Vartuli* + "directed to the specific trading account") → likely requires CTA registration + NFA membership. |
| **Posture we engineer toward** | Signals are **informational and subscriber-initiated**; the subscriber exercises independent judgment and places (or declines) their own trade. No platform-side discretion. No per-account sizing keyed to the subscriber's balance. |
| **Hard requirement** | Every public display of paper/simulated/backtested performance carries the **CFTC Reg. 4.41(b)** prescribed hypothetical-performance disclaimer, **net of fees**, not misleading (NFA Rule 2-29) — as voluntary anti-fraud hygiene, **without conceding CTA status**. |
| **Watch item** | Marketplace revenue-share with third-party creators amplifies **NFA Bylaw 1101** exposure for our carrying FCM (Tradovate) if any participant is an unregistered CTA. |

---

## 1. The threshold problem (stated honestly)

A "commodity trading advisor" (CTA) is defined at **CEA § 1a(12) / 7 U.S.C.
§ 1a(12)** as anyone who, *for compensation or profit*, advises others —
**"either directly or through publications, writings, or electronic media"** —
as to the value or advisability of trading futures, or who issues analyses or
reports concerning such trading. Registration is mandatory under **CEA § 4m(1)
/ 7 U.S.C. § 6m(1)** unless an exemption applies.

**Honest read:** a $29–$99/mo subscription delivering MNQ/CL/MES/NQ buy/sell
signals *meets the prima facie CTA definition.* The phrase "software tool
provider" does **not**, by itself, remove us from § 1a(12). Our position must
rest on an **exemption** (§ 4.14(a)(9)) backed by **First Amendment publisher
precedent** — not on a claim that we fall outside the definition entirely.

The de minimis exemption **§ 4.14(a)(10)** (≤15 persons advised, not held out
to the public) is **unavailable** to a public subscription marketplace. We do
not rely on it.

---

## 2. The defense — *Lowe / Taucher* + Reg. 4.14(a)(9)

**CFTC Reg. 4.14(a)(9)** exempts a person whose advice is **"not directed to
the specific trading accounts of subscribers"** and not **"based on, or
tailored to, the … positions or other circumstances or characteristics of
particular clients."** This is the regulatory codification of the publisher
line of cases:

- **_Lowe v. SEC_, 472 U.S. 181 (1985).** Bona fide, impersonal investment
  newsletters fall within the publisher exclusion and are not "investment
  advisers," even when they contain specific buy/sell recommendations.
  Congress regulates **personalized** advice, not non-personalized publishing.
  *Cuts FOR us.*

- **_Taucher v. Born_, 53 F. Supp. 2d 464 (D.D.C. 1999).** Applying *Lowe*,
  the court held the CTA-registration requirement **unconstitutional as applied
  to publishers** of books, newsletters, websites, written "trading systems,"
  and **software** — because they never traded for clients, never exercised
  discretion, and had no personalized client contact. This is the **direct
  CFTC analogue** and the ancestor of the § 4.14(a)(9) safe harbor.
  *Cuts FOR us — strongly, and squarely in the futures context.*

**Therefore:** when AlgoChains broadcasts an **identical signal to all
subscribers**, who each independently decide whether to act, we look like the
*Lowe* newsletter / the *Taucher* software publisher — protected speech, no CTA
registration required. **This is the position the product is engineered to
preserve.**

---

## 3. The liability — *CFTC v. Vartuli* and the auto-execution line

**_CFTC v. Vartuli_, 228 F.3d 94 (2d Cir. 2000)** is the case we must
distinguish. The "Recurrence" system issued **real-time mechanical buy/sell
signals** and told users to **follow them mechanically, without interpretation
("there was nothing to interpret")**. The Second Circuit held those signals
were **not protected speech** — they functioned as commands to a machine-like
user — and the operator could be **required to register as a CTA**. The court
noted the result might differ had the program been disseminated **as
information/opinion for the user to evaluate**.

**The dispositive line:**

| Protected — no registration (*Lowe / Taucher*) | Unprotected → CTA (*Vartuli*) |
|---|---|
| Impersonal, **published** signal/analysis | Real-time **mechanical** signal |
| Subscriber **exercises independent judgment** | Followed **without judgment** |
| No account-specific contact | **Directed into / executed in** the account |

**Our principal risk is the live copy-trade / auto-execution feature.** If
signals are auto-routed into, or sized to, a specific subscriber's account, the
advice becomes "directed to the specific trading account," **defeating
§ 4.14(a)(9)** and landing us in *Vartuli* — likely requiring CTA registration
+ NFA membership, possibly CPO/IB/AP issues.

### 3.1 Engineering mandate that follows

The codebase is built to keep us on the protected side of the *Vartuli* line:

1. **Signals are informational and subscriber-initiated.** Copy-trade signals
   are delivered for the subscriber to act on; the **hosted account is PAPER**
   (simulated), not a live brokerage account we direct.
2. **No platform discretion.** The subscriber chooses to join a bot
   (`join_bot`), chooses their own `size_multiplier`, and must **explicitly
   acknowledge the futures risk disclosure** (`accept_subscriber_terms`) before
   any copy-trade is active. Provisioning leaves the default assignment
   **paused**.
3. **No per-account tailoring by the platform.** Sizing is a subscriber-set
   multiplier, not platform logic keyed to the subscriber's balance/profile.
4. **Live execution stays in the subscriber's hands.** Any future live tier
   must route the *decision and the order placement* to the subscriber, not
   auto-execute on the platform's discretion. `realized_pnl` live hooks
   (migration `20260529`) segregate `is_live` fills and are gated behind
   explicit live-tier + risk consent.

> **Recommendation for counsel review:** confirm whether the live copy-trade
> execution is structurally **subscriber-initiated** (defensible) vs.
> **platform-directed** (registration likely). This single fact is, per the
> precedent, close to dispositive.

---

## 4. Hypothetical / simulated performance — Reg. 4.41(b) (hard requirement)

Because we market a **hosted paper account** and (via the marketplace)
**backtested/simulated strategy results**, every public presentation of
paper/simulated/backtested performance must:

1. Carry the **CFTC Reg. 4.41(b)** prescribed hypothetical-performance
   disclaimer (codified verbatim in `compliance/disclosures.py` as
   `HYPOTHETICAL_PERFORMANCE_DISCLAIMER`; **confirm against eCFR before any
   client-facing legal reliance**).
2. Be presented **net of all commissions, fees, and expenses** (NFA Rule
   2-29(c)).
3. Not be misleading in overall impact (NFA Rule 2-29(b)).

**Framing:** we apply these as **voluntary anti-fraud hygiene**. The CEA
anti-fraud provisions (**§ 4o / § 4b**) reach even some unregistered persons, so
the disclaimer protects us **without conceding** registered-CTA status. Reg.
4.41 / Rule 2-29 are member obligations; using their language defensively is not
an admission that we are a member.

**Engineering:** `with_hypothetical_disclaimer()` is attached to every
paper/simulated performance payload (paper P&L, paper portfolio, marketplace
backtested metrics). The general `PAST_PERFORMANCE_DISCLAIMER` remains on all
performance outputs; the stricter 4.41(b) text is added wherever results are
**simulated/paper/backtested**.

---

## 5. The "software tool provider" lane — where it holds and where it doesn't

**CFTC Letter 25-50 (No-Action, Dec. 19, 2025, Phantom Technologies)** confirms
a **passive technology service vendor** lane: software that **passively enables
user-initiated transactions** can avoid registration. But the lane protects
*neutral* front-ends — **not** products whose value proposition is **generating
trade signals**. *Vartuli* and CFTC staff treat signal generation / marketing a
trading program as **advice**.

**Conclusion:** the "tool provider, not a CTA" label is:
- **Strong** for genuinely passive components — the charting/order-entry UI and
  the **paper-trading sandbox** itself.
- **Weak** precisely where we earn revenue — signals + copy-trade — unless we
  stay inside § 4.14(a)(9) (impersonal, subscriber-initiated).

We therefore **do not over-rely** on the tool-provider framing; we anchor on
§ 4.14(a)(9) + *Lowe/Taucher* and engineer to preserve it.

---

## 6. Marketplace & NFA Bylaw 1101 (indirect but business-critical)

**NFA Bylaw 1101** forbids an NFA member (e.g., our carrying FCM, Tradovate)
from doing business with a non-member that **should be** registered. It is
**strict liability**. If AlgoChains — or a third-party marketplace creator
earning a revenue share — is in fact an unregistered CTA, our **broker can be
forced to terminate the relationship**: a business-ending risk independent of
any CFTC action.

**Controls:**
- Marketplace creators are surfaced as **impersonal published strategies**, not
  personalized advisory relationships.
- The creator-payout ledger (`20260528`) records revenue share but does **not**
  create platform discretion over subscriber accounts.
- **Recommendation:** require creators to represent their registration/exemption
  status (collect a written § 4.14(a)(9) self-certification at onboarding) — a
  Bylaw 1101 due-diligence analogue.

---

## 7. Controls implemented in this codebase (traceability)

| Legal basis | Control | Where |
|---|---|---|
| § 4.14(a)(9) impersonal advice | Signals identical to all; subscriber-initiated; no per-account platform sizing | `subscriber_tools.py`, `trade_propagation.py` |
| *Vartuli* avoidance | Explicit risk acknowledgment gates copy-trade; default assignment **paused**; hosted account is **paper** | `accept_subscriber_terms`, `join_bot` consent gate, provision `paused=True` |
| Reg. 4.41(b) | Prescribed hypothetical-performance disclaimer on all simulated/paper/backtested results | `compliance/disclosures.py` (`HYPOTHETICAL_PERFORMANCE_DISCLAIMER`) |
| NFA Rule 2-29 | Past-performance / not-advice disclaimer on every performance output; net-of-fees presentation | `compliance.disclosures.with_disclaimer` |
| Anti-fraud (§ 4o/§ 4b) | Audited, versioned consent; append-only consent log | `subscriber_consent_log`, migration `20260525` |
| Bylaw 1101 | Creator revenue-share ledger w/o platform discretion; (recommended) creator exemption self-cert | migration `20260528` |
| Live-execution risk segregation | `is_live` fills segregated; live gated behind explicit live-tier + consent | migration `20260529` |

---

## 8. Outside counsel guidance received (Start to Finish Law — privileged)

Outside counsel (Eric R. Preston, Start to Finish Law, PLLC) reviewed the
architecture and product (May 2026). Key guidance, which this codebase is built
to honor:

- **Compensation:** *"Avoid performance fees and transaction-based compensation
  in favor of flat fees."* → Implemented: performance fees are **disabled by
  default** (`ALGOCHAINS_PERFORMANCE_FEE_RATE=0.0`); revenue is flat subscription
  + usage. **Do not enable performance fees without counsel sign-off.**
- **Framing:** *"Avoid claims of facilitating/automating trades and focus on
  signals publishing."* *"'Publishing of ideas and providing technology tools'
  framing will fare better long term than 'curated strategies and automated
  trading.'"* → Product copy and tool descriptions should describe **signals the
  subscriber chooses to act on**, not platform-automated execution.
- **No discretion / user control:** verify users select algos, review info, set
  allocations/parameters, and can start/stop anytime. → Subscriber-initiated
  `join_bot`/leave, subscriber-set sizing, paused-by-default, explicit risk
  acknowledgment. Leaderboards "draw more scrutiny."
- **Substantiation:** keep logs/DB records backing every measurable claim
  (win rate, Sharpe, returns) and run periodic audits. → Audit trails +
  net-of-fees presentation + §4.41(b)/2-29 disclaimers.
- **Credentials:** trade-only API permissions (no withdrawal), OAuth over stored
  keys, encryption, vault, 2FA; study the **3Commas** incident.
- **Scope:** US-only at launch; assets = Equities/Options/Crypto first, FOREX +
  Futures soon after. (The live MNQ/CL/MES/NQ bots are the platform's *own*
  trading; the regulated surface is advice/signals to subscribers.)
- **Recent enforcement (counsel-cited):** **CFTC Release 8770-23 (2023)** — a
  platform offering trade **signals *plus* automating trading** was found to be
  an **unregistered CTA** (permanent injunction; ~$100k). Note the **Pham
  dissent** and guidance that pure tech providers of signals + order-submission
  software may not require licensing — an **evolving, grey area**. Counsel's
  steer: be conservative in structure and marketing; *"consider not taking
  compensation until growth is sufficient to mitigate risk further — you can
  always flip that switch later."*
- **Real-world signal:** **Alpaca denied AlgoChains's API application**,
  classifying the platform as a **copy-trade system** (against their policy,
  May 2026). Concrete evidence that the "automated copy-trade" framing carries
  real consequences — reinforcing the signals-publishing posture.

> This section summarizes privileged attorney–client communications for internal
> engineering traceability only. It is not a substitute for counsel's full
> written advice, and the T&Cs / Privacy Policy drafts were still in progress.

## 9. Open questions for counsel (priority order)

1. **Is live copy-trade subscriber-initiated or platform-directed?** (Likely
   dispositive under *Vartuli*.)
2. Confirm the **verbatim Reg. 4.41(b)** text and any 2024–2026 amendment to
   Reg. 4.41 / NFA IN 9025 (the major 2024 action was Reg. **4.7**, a different
   rule).
3. Do we need a **§ 4.14(a)(9) notice filing** or is the exemption self-executing
   for our structure?
4. Does the marketplace revenue-share create **CPO/pool** characteristics?
5. **Bylaw 1101** posture with Tradovate — do we need a written exemption
   representation on file with the FCM?

---

## Sources (verify against primary text before legal reliance)

- 7 U.S.C. § 1a(12) (CTA definition); CEA § 4m(1) / 7 U.S.C. § 6m(1) (registration & de minimis)
- 17 C.F.R. § 4.14(a)(9), § 4.14(a)(10); 65 Fed. Reg. 12938 (Mar. 10, 2000) adoption of 4.14(a)(9)
- *Lowe v. SEC*, 472 U.S. 181 (1985)
- *Taucher v. Born*, 53 F. Supp. 2d 464 (D.D.C. 1999); subsequent history *Taucher v. Brown-Hruska*, 396 F.3d 1168 (D.C. Cir. 2005)
- *CFTC v. Vartuli*, 228 F.3d 94 (2d Cir. 2000)
- 17 C.F.R. § 4.41(b) (hypothetical-performance disclaimer); 72 Fed. Reg. 7806 (Mar. 26, 2007)
- NFA Compliance Rule 2-29; NFA Interpretive Notice 9025; NFA Bylaw 1101 & Interpretive Notice 9007
- CFTC Letter No. 25-50 (No-Action, Dec. 19, 2025, Phantom Technologies)
- CEA anti-fraud §§ 4b, 4o
