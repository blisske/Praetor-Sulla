# Foundation Terms of Service

**Version:** 2026-05-22
**Operator:** Foundation Bots (sole operator, Colorado, USA)
**Contact:** support@foundationbots.com

---

## 1. What Foundation Is

Foundation ("the Service") is personal algorithmic-trading software. The
Service connects to your own brokerage account at Kraken (or other
supported venues as added) using API keys YOU generate and provide, and
runs an autonomous trading strategy on your behalf within the safeguards
documented in the dashboard.

Foundation is software, not advice. Specifically, Foundation:

- Is **not** a registered investment adviser, broker-dealer, commodity
  trading adviser, or money transmitter under U.S. or any other
  jurisdiction's law.
- Does **not** provide personalized investment recommendations.
- Does **not** hold custody of your funds. Your money stays in your own
  brokerage account at Kraken (or other connected venues). Foundation
  cannot deposit, withdraw, or transfer your funds.
- Does **not** guarantee any return, performance, or outcome.

If you need personalized financial advice, consult a licensed adviser.
If you need tax advice, consult a CPA or tax attorney. Foundation does
not provide either.

---

## 2. Eligibility

By creating an account, you represent that you:

- Are at least 18 years old (or the age of legal majority in your
  jurisdiction).
- Have the legal capacity to enter into this agreement and to operate
  a brokerage account.
- Are not located in, or a citizen/resident of, any jurisdiction where
  using algorithmic-trading software or operating cryptocurrency
  brokerage accounts is prohibited.
- Will comply with all applicable laws, tax obligations, and
  brokerage-venue terms of service in your jurisdiction.

Foundation may refuse, suspend, or terminate accounts at any time at
its sole discretion, including for suspected violations of these
Terms, brokerage TOS violations, abuse, fraud, or any other reason.

---

## 3. Bring-Your-Own-Key (BYOK) Model + Your Responsibilities

Foundation connects to your brokerage account using API keys you
generate on the brokerage side and paste into the Service. By providing
those keys, you authorize the Service to use them to:

- Read market data and your account balance/positions
- Place, modify, and cancel orders on your behalf within the
  trading strategy you have configured
- Do **nothing else** outside the scope you grant on the brokerage's
  permission UI

You agree that:

- **You will NOT grant Withdraw permissions** to any API key you provide
  to Foundation. Foundation does not need this scope. A key without
  withdraw scope cannot drain your account if compromised. If you
  ignore this guidance, the operator has no way to prevent unauthorized
  withdrawals and accepts no responsibility for them.
- **You are responsible** for the safety of your brokerage account
  outside of Foundation (your brokerage password, 2FA, IP allowlists,
  device security, etc.).
- **You may revoke** the key on the brokerage side at any time, which
  immediately disables Foundation's ability to trade for you.
- **Read the security guidance** displayed on the broker-connection
  page in the dashboard. You explicitly acknowledged this guidance
  before pasting your key.

---

## 4. Encryption + Operator Access

Your brokerage API keys are encrypted at rest using AES-256-GCM with a
master key held by the operator. The encrypted blobs are stored in the
Service's database; the master key is stored on the operator's host
filesystem.

**Important transparency:** Because Foundation is a self-hosted, single-
operator system, the operator (Foundation Bots) has technical ability
to decrypt any user's stored brokerage keys. The operator commits to
not doing so except as needed to debug your account at your specific
request, to investigate a credible security incident, or as required
by valid legal process.

You acknowledge this access model and agree that Foundation is not
liable for any consequence arising from:

- The operator's lawful decryption of your keys for the purposes above
- Compromise of the operator's host or database that exposes encrypted
  blobs and/or the master key
- Any subsequent misuse of your keys by an attacker who obtained them
  via such a compromise

If you require a custodial-or-hardware-key trust model, do not use
Foundation.

---

## 5. Trading Risk + No Warranty of Results

Trading any financial asset involves risk of loss, including loss
of your entire capital. Algorithmic trading concentrates that risk: a
bug, market dislocation, exchange outage, network failure, race
condition, or unforeseen strategy interaction can produce losses
larger and faster than manual trading would.

By using Foundation you agree that:

- You may lose money. You may lose all the money in your brokerage
  account. You alone bear that loss.
- Backtested or shadow-mode (paper) results do not predict live results.
- The Service may have bugs, downtime, or unexpected behavior at any
  time. Risk safeguards (drawdown halts, position caps, circuit
  breakers, frozen-mode kill switches) are best-effort and may fail.
- You are responsible for monitoring your account and intervening
  (freezing, killing the bot, revoking the API key) if you observe
  behavior you do not understand or do not approve of.
- Past performance, simulated performance, and the strategy
  description in the dashboard are **not** a promise, projection, or
  forecast of future results.

---

## 6. Service Availability + Modifications

Foundation is provided "as-is" and "as-available". No uptime SLA is
offered. The Service may be unavailable during maintenance, outages,
upstream dependencies (Kraken, LM Studio, network providers), or for
no stated reason.

The operator may modify, suspend, or discontinue any feature of the
Service at any time, including changes to:

- Trading strategy, paradigms, signals, or risk parameters
- Supported brokerages or trading venues
- Pricing, billing, or account tiers
- These Terms (with notice via email + in-app banner)

Material changes to these Terms will be announced at least 14 days
before they take effect, and will require you to re-accept on next
login (the dashboard surfaces a re-acceptance modal when your last
accepted version differs from the current version).

---

## 7. Limitation of Liability

**TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW**: in no event
shall Foundation Bots, the operator, any contributor, or any affiliate
be liable to you or to any third party for any indirect, incidental,
special, consequential, exemplary, or punitive damages, including
without limitation loss of profits, loss of trading capital, loss of
data, loss of business opportunity, or any other intangible loss,
arising out of or in connection with your use of, or inability to use,
the Service, even if Foundation Bots has been advised of the
possibility of such damages.

**THE AGGREGATE LIABILITY** of Foundation Bots to you for all claims
arising out of or relating to the Service shall not exceed the
greater of (a) the total fees you have actually paid Foundation Bots
for the Service in the twelve months preceding the event giving rise
to the claim, or (b) one hundred U.S. dollars ($100). For the free
tier (no fees paid), the aggregate cap is $100.

Some jurisdictions do not allow exclusion or limitation of certain
damages. To the extent that such law applies to you, the above
limitations apply only to the maximum extent permitted by that law.

---

## 8. Indemnification

You agree to indemnify, defend, and hold harmless Foundation Bots, the
operator, and any contributor from and against any and all claims,
liabilities, damages, losses, costs, and expenses (including
reasonable attorneys' fees) arising out of or in connection with:

- Your use of, or inability to use, the Service
- Your violation of these Terms
- Your violation of any third party's rights, including any
  brokerage venue's terms of service
- Your violation of any law, rule, or regulation in your jurisdiction,
  including tax, securities, or commodities regulation
- Any trading activity executed via the Service on your account

---

## 9. Dispute Resolution; Governing Law

These Terms are governed by the laws of the State of Colorado, USA,
without regard to its conflict-of-law principles.

Any dispute, claim, or controversy arising out of or relating to these
Terms or the Service shall be resolved by **binding arbitration**
administered by the American Arbitration Association under its
Consumer Arbitration Rules. The seat of arbitration shall be Denver,
Colorado, USA. Arbitration shall be conducted in English by a single
arbitrator. Either party may seek interim relief from a court of
competent jurisdiction in Denver, Colorado in aid of arbitration.

**CLASS-ACTION WAIVER:** You agree that any dispute shall be brought
in your individual capacity only, and not as a plaintiff or class
member in any purported class, collective, or representative action.

If for any reason the arbitration clause above is held unenforceable,
the exclusive jurisdiction and venue for any dispute shall be the
state and federal courts located in Denver, Colorado.

---

## 10. Termination

You may terminate your account at any time via the dashboard's
account-deletion flow. Doing so soft-deletes your account; your data
is retained for 30 days in a deleted-state directory before permanent
removal, in case you change your mind.

Foundation Bots may suspend or terminate your account at any time for
any reason, with or without notice. Upon termination, your right to
use the Service ceases immediately. Sections 4, 5, 7, 8, 9, and 11
of these Terms survive termination.

---

## 11. Miscellaneous

- **Entire agreement.** These Terms (together with any other policies
  expressly referenced) constitute the entire agreement between you
  and Foundation Bots regarding the Service.
- **No waiver.** Failure to enforce any provision is not a waiver of
  that provision.
- **Severability.** If any provision is held unenforceable, the
  remaining provisions remain in full effect.
- **Assignment.** You may not assign these Terms. Foundation Bots may
  assign these Terms to a successor entity (e.g. on sale or merger).
- **No third-party beneficiaries** except the operator's affiliates
  and contributors as indemnitees under Section 8.

---

## Acknowledgment

By creating an account, you acknowledge that you have read, understood,
and agree to be bound by these Terms. By providing brokerage API keys,
you separately acknowledge the BYOK + operator-access disclosures in
Sections 3 and 4.

Questions or concerns: **support@foundationbots.com**.
