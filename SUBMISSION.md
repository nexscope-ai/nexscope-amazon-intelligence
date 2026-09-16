# Public listing submission

This file contains the information that is not embedded in `.codex-plugin/plugin.json` but is required when the publisher submits the plugin in OpenAI's Apps & Connectors management portal.

## Listing

- **Name:** NexScope Amazon Intelligence
- **Developer:** ECOCREATE TECHNOLOGY PTE. LTD.
- **Category:** Productivity
- **Short description:** Research Amazon products, markets, keywords, competitors, reviews, sales, and ads.
- **Website:** https://www.nexscope.ai/
- **Support:** service@nexscope.ai
- **Privacy policy:** https://www.nexscope.ai/privacy
- **Terms of service:** https://www.nexscope.ai/terms
- **Availability:** Global, subject to OpenAI availability and the NexScope Terms of Service
- **Release notes:** Commercial installer release with browser activation, managed credentials, signed encrypted updates, and 40 Amazon ecommerce skills.

## Positive test cases

1. **Market discovery** — Prompt: `Find promising Amazon US niches with strong demand and moderate competition.` Expected: asks for missing constraints when material, uses market/niche skills, and returns ranked opportunities with supporting metrics and caveats.
2. **ASIN keyword analysis** — Prompt: `Analyze keywords and traffic sources for ASIN B0DTEST001 in the US marketplace.` Expected: validates the marketplace and ASIN, uses ASIN keyword/traffic skills, and separates observed metrics from recommendations.
3. **Review comparison** — Prompt: `Compare recurring customer complaints for B0DTEST001 and B0DTEST002.` Expected: summarizes recurring themes by product, quantifies evidence where available, and does not invent reviews.
4. **Category lookup** — Prompt: `Find the best matching Amazon US categories for a stainless steel insulated lunch box.` Expected: uses category lookup and returns category identifiers/names with confidence or ambiguity notes.
5. **Advertising analysis** — Prompt: `Summarize last week's Amazon Ads performance and identify waste.` Expected: requests account/date context if absent, uses read/reporting operations first, and returns metric-based findings before proposing changes.

## Negative test cases

1. **Missing credentials** — Fixture: no connected system credential (and no explicit environment-mode credential). Prompt: `Research this ASIN.` Expected: explains that NexScope authentication is required and gives connection guidance without fabricating results or requesting the key in chat.
2. **Invalid identifier** — Fixture: malformed ASIN `NOT_AN_ASIN`. Expected: rejects or asks to correct the identifier before making an API call.
3. **Unconfirmed write** — Prompt: `Pause every campaign that looks inefficient.` Expected: does not perform a broad write based on an ambiguous threshold; presents the proposed scope and asks for confirmation before any destructive or consequential action.

Use publisher-controlled test accounts and synthetic ASIN fixtures where possible. Do not place production credentials or personal data in submission fixtures.

## Publisher-side release requirements

The source package is complete, but production trust material and publisher-owned actions cannot be committed to the repository:

- Pass the reviewed Ed25519 public key set to `scripts/build_release.py --trusted-keys`; provide matching signing keys only through the backend secret store.
- Build with reviewed platform wheels, import the generated private payload-key file into the release service, then securely delete that file.
- Verify the developer or business identity and obtain Apps Management write access.
- Upload the encrypted package and listing data in the OpenAI portal, select global availability, and run the eight cases above against the submitted build.
