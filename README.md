# Nature Exposure Snapshot

A first check of whether planned water company work could affect protected nature in England. This prototype covers Cambridgeshire and Peterborough.

A project team describes their work and shows where it is. The tool screens each part against public Natural England data, assigns an indicative assurance level from A1 to A4, and explains why. AI then helps the user understand and explore the results.

**Indicative screening from public data, not a regulatory decision.**

## The Idea

Water companies run many projects each year: new sewers and pipelines, storm overflow upgrades, treatment works. Early on, project teams need to know whether proposed work could affect protected nature, and how much further assessment it is likely to need.

Nature Exposure Snapshot is designed as a first step for a water company considering Mozaic's services. A project team can:

1. Describe the work by chatting with an assistant or filling in a short form.
2. Show where it is by entering a postcode, uploading coordinates or drawing on a map.
3. See which parts of the project touch protected nature, with an indicative assurance level and a plain-English reason for each.
4. Read a written report, or ask follow-up questions about the results.

This gives them a sense of what Mozaic would do for them before they ask for a quote or book a call. If Mozaic would rather not show this much to clients up front, the same tool works internally: clients supply the project details, and Mozaic's team uses it to triage each proposal before specialist work begins.

**What it covers.** Seven types of water company work: new pipelines and sewers, pipe upgrades, storm overflows and outfalls, treatment works with combined heat and power (CHP), sludge and anaerobic digestion, storm tanks and larger works, and new or increased abstraction. It screens against SSSIs, SACs, SPAs, Ramsar sites, ancient woodland, priority habitat and Natural England's Impact Risk Zones, across Cambridgeshire and Peterborough plus a margin (97 × 100 km).

## The Thinking

### AI for language, code for decisions

A screening result needs to be consistent and explainable. The same project should always get the same result, and every level should have a reason someone can check. So **AI never decides an assurance level.**

The screening engine applies fixed rules, kept in one readable file (`app/rules.yaml`), to the project details and the public data. AI sits around that core, doing what benefits from language understanding: interpreting how people describe their work, explaining the results, and answering follow-up questions. Everything the AI produces is checked by code before the user sees it.

```text
Project description (chat) or form
        ↓
AI intake → five confirmed answers + location
        ↓
Deterministic screening engine
  ├── Natural England Impact Risk Zones
  ├── Protected sites and habitats
  └── Rules in rules.yaml
        ↓
A1–A4 result for each part of the project
        ↓
AI report and results assistant
```

Because of this, the screening, map, form and a facts-only report all work with no AI key at all.

### The assurance levels

The prototype uses four levels based on Mozaic's A1–A4 assurance scale:

| Level | Meaning |
|---|---|
| **A1** | Nothing found. A desk check to confirm is enough. |
| **A2** | An ecologist should review this from records. |
| **A3** | Specialist assessment needed, likely with Natural England input. |
| **A4** | Specialist assessment plus a site survey. |

How a project is assigned to these levels is my estimate for this prototype, not Mozaic's methodology. It would need calibrating with Mozaic and an ecologist before real use.

What triggers each level:

| Level | Triggered by |
|---|---|
| **A3** | Inside an SSSI, SAC, SPA or Ramsar site · within 15 m of ancient woodland · a risk zone where Natural England asks to see almost any development · any risk-zone trigger when the work involves abstraction or dewatering |
| **A2** | A risk-zone trigger for this type of work · overlapping priority habitat · a broader risk-zone trigger, when the work goes outside land the company already operates |
| **A1** | None of the above |
| **A4** | Any A3 part, when the client requires a site survey |

Every rule is checked, the highest level wins, and every reason is shown.

The user's five answers decide which rules apply:

- **Type of work** decides which risk-zone rules are read. A storm overflow is checked against discharge rules, a new pipeline against infrastructure rules.
- **Working outside land you already operate** brings in broader risk-zone rules about farmland, woodland and water.
- **Abstraction and dewatering** raise any risk-zone trigger from A2 to A3, because moving water can affect wetlands and rivers well beyond the works.
- **Site survey required** isn't a judgement about risk. Some clients require an in-person survey regardless. If they do, any part already needing specialist assessment (A3) becomes A4, following Mozaic's own description of A4 as a visit for clients who require one.

For example, a pipeline section that triggers a risk zone and overlaps priority habitat is A2. If the work needs dewatering, the risk-zone trigger raises that section to A3.

### Keeping the screening deterministic

Natural England's Impact Risk Zones set out, for every location, which kinds of development could affect a nearby SSSI. Each zone carries a 13-digit code, one digit per type of development. The value sets how large, or what kind of, development must be before Natural England wants to be consulted; 0 means that type doesn't trigger. The code isn't documented, so I decoded it from Natural England's online tool, confirmed the digit order against their published guidance, and read the advice for every value used in the area. I then mapped the relevant digits to each work type and stored the trigger values as data in `rules.yaml`, rather than in application code.

Protected sites and habitats are checked against their mapped boundaries. These datasets carry no rules of their own, so their levels are based on legal status and published guidance. SSSIs and European sites are legally protected, and work in or near them usually needs Natural England's involvement (SSSI assent, or a Habitats Regulations Assessment for European sites). The 15 m ancient woodland buffer comes from Natural England and Forestry Commission standing advice, used here as a screening rule.

Each part of a project is screened separately. Pipelines are split into 50 m sections of a 30 m wide corridor, points become circles of 50 m radius, and areas are cut wherever a protected site, priority habitat, risk zone or the woodland buffer crosses them. Neighbouring sections with the same level are merged, so the map shows exactly which stretch drives the result.

The full code mapping, thresholds, assumptions and data preparation are in [`docs/technical_notes.md`](docs/technical_notes.md).

### Where the AI is used

**Intake chat.** It turns a free-text description into the five answers the screening needs. It asks project-specific follow-up questions ("Will the trench be deep enough that you'd need to pump out groundwater?"), explains terms, and checks anything it has inferred. It can only record allowed answers, the user confirms a summary before screening runs, and it can't invent a location or mention a level. A structured form is available for users who prefer it.

**Report.** It turns the results into findings, next steps and caveats, working only from a plain-English summary of the deterministic results. Code checks that every part it mentions exists, every level it states is correct, and every part above A1 is covered. If it fails after one retry, or no AI is available, a report built directly from the results is shown instead.

**Results assistant.** It answers questions such as "Which stretch is the problem?" or "What would change if we didn't need dewatering?". Its tools call the screening engine itself, so a "what if" is re-screened rather than guessed. Any level it states is checked against what the tools returned.

Next steps also come from code. Each level and rule has a fixed next step in rules.yaml, including how Mozaic would handle it, and the AI builds its advice from these rather than inventing its own.

### Models

| Where | Model | Used for |
|---|---|---|
| In the app | Claude Sonnet 5.5 | Intake chat, report and results assistant |
| In the app | Gemini (free tier) | Optional alternative for the chat and report; not tested since the redesign |
| Development | Claude Code (Claude Opus 5.5) | Implementation, testing and debugging |
| Development | Claude (chat) | Planning, challenging design decisions and writing implementation instructions |

### How I steered the agent

I kept the system coherent through documented decisions, precise instructions and automated checks.

- **Decide before building.** I designed each major part in conversation first, recorded the reasoning in `DECISIONS.md`, then gave Claude Code a specific task with checks to run.
- **Keep rules as data.** Levels, distances and triggers live in `rules.yaml`, not in application code or prompts. The screening logic can be inspected, and the AI layer can change without any screening result changing.
- **Verify rather than trust.** The agent's first reading of the undocumented risk-zone codes was treated as a hypothesis, and checked against Natural England's own tool before anything was built on it.
- **Test at each stage.** 128 automated tests run offline, including a regression test on 461 real Anglian Water storm overflow locations.

### What changed during development

- **The intake was too rigid.** The first version asked questions such as "outside the existing site boundary?" and sometimes asked for information the description already contained. I replaced it with a conversational intake, keeping a separate form for structured input.
- **The first AI feature didn't add enough value.** AI was mainly filling in five answers, which a form can do more reliably. I added the report and results assistant, where AI explains results and explores scenarios in ways a form can't.
- **The first version only screened fixed example sites.** I redesigned it around user-defined projects, with pipelines screened section by section.
- **The free Gemini path was unreliable.** Claude became the default, with a per-session message cap to control cost.

## Reflections

**Time.** I spent around 11 hours on this, beyond the suggested 4–6. A six-hour version would have been the deterministic screening, the map and the intake chat. I chose to spend the extra time on the report and results assistant, because that's where AI adds the most for the user: explaining and exploring results, not just collecting inputs.

**Current limitations**

- Other work types, such as reservoirs and pumping stations, aren't supported yet.
- Rivers and streams, protected species and local wildlife sites aren't checked.
- Direction of effect, such as whether work is upstream or downstream of a site, isn't assessed.
- Only Cambridgeshire and Peterborough are covered, although the data pipeline isn't specific to the county.
- Project scale, such as discharge volume or CHP size, is assumed rather than asked.
- Whether a site is outside a settlement, which some risk-zone rules depend on, isn't checked.
- Only postcodes are looked up, not place names.
- One set of answers applies to every site in a project, so dewatering needed at one wet stretch affects every stretch with a risk-zone trigger.
- The data is a snapshot from 2 October 2026, and Natural England updates the risk zones every two months.

**Limitations of the AI integration**

- The results assistant depends on Claude, because it uses multi-step tool calls.
- The intake can misread a vague description. The confirmation step guards against this, but only if the user checks it.
- The report's facts are checked against the screening results, but its wording is still written by AI.
- The facts-only report shown without an AI key is accurate but plain and repetitive.
- The 20-message cap per browser session covers the chat, report and assistant together. It's stored in memory, so it resets when the app restarts or the user clears their cookies. It controls cost in a prototype; it isn't a security measure.

**What I would do next**

1. Ask for project scale where it affects the result, and allow different answers for different sites.
2. Add rivers, construction method and place-name search.
3. Refresh the data automatically and re-run the regression tests.
4. Have an ecologist review results against their own judgement.
5. Calibrate the levels and rules with Mozaic's team before any real-world use.

## Setup

Requires Python 3.11. The prepared data is included, so no data needs downloading. The prototype runs without an API key.

### Install

```bash
git clone https://github.com/finlaymilller40/mozaic-nature-snapshot.git
cd mozaic-nature-snapshot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

To enable all AI features, add a Claude API key to `.env` as `ANTHROPIC_API_KEY`.

### Run

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

Then open http://localhost:8000.

### What works with which key

| Configuration | Available |
|---|---|
| No API key | Form, screening, map and facts-only report |
| Gemini key with `LLM_PROVIDER=gemini` | Adds the intake chat and AI report (not tested since the redesign) |
| Claude key | All features, including the results assistant |

### Tests

No API key or internet connection needed:

```bash
.venv/bin/python -m pytest
```

To rebuild the data from the raw downloads, see [`docs/technical_notes.md`](docs/technical_notes.md).

## Data and licences

- Natural England data © Natural England copyright, containing Ordnance Survey data © Crown copyright and database right 2026, licensed under the Open Government Licence v3.0.
- Storm overflow data © Environment Agency.
- Postcode lookups by postcodes.io.
- Map data © OpenStreetMap contributors (ODbL).

The code is released under the [MIT License](LICENSE).
