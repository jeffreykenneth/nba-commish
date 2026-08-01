# Phase 0: Integration and Feasibility Spike

## 1. Purpose

Phase 0 will confirm that the dashboard can reliably obtain and combine the required data before the full application is developed.

The phase should answer five questions:

1. Can the selected Yahoo fantasy league, teams, standings, and rosters be retrieved?
2. Can Yahoo trade data identify transactions involving future draft picks?
3. Can Hoopshype salary data be extracted consistently?
4. Can Yahoo and Hoopshype players be matched accurately?
5. Can the lottery and draft-order rules be reproduced from test data?

Phase 0 should produce working scripts, saved sample data, documented findings, and recommendations. It does not need a polished user interface.

---

# 2. Scope

## Included

* Yahoo developer application and OAuth setup
* Yahoo league discovery
* Team, standings, roster, and transaction extraction
* Investigation of Yahoo draft-pick trade data
* Hoopshype salary extraction
* Multiple-contract salary aggregation
* Initial player-name matching
* Draft lottery proof of concept
* Draft-pick ownership proof of concept
* Saved sample payloads
* Findings and implementation recommendations

## Not included

* Production dashboard UI
* Keeper selection interface
* User management
* Automated scheduled imports
* Full historical season support
* Production deployment
* Comprehensive manual correction screens
* Final database schema
* Complete draft board

---

# 3. Recommended Phase 0 repository structure

```text
phase-0/
├── README.md
├── .env.example
├── pyproject.toml
├── scripts/
│   ├── yahoo_auth.py
│   ├── yahoo_list_leagues.py
│   ├── yahoo_import_league.py
│   ├── yahoo_import_transactions.py
│   ├── hoopshype_import.py
│   ├── match_players.py
│   └── generate_test_draft.py
├── src/
│   ├── yahoo/
│   │   ├── client.py
│   │   ├── auth.py
│   │   ├── parsers.py
│   │   └── models.py
│   ├── hoopshype/
│   │   ├── client.py
│   │   ├── parser.py
│   │   └── models.py
│   ├── matching/
│   │   ├── normalize.py
│   │   ├── matcher.py
│   │   └── aliases.py
│   └── draft/
│       ├── lottery.py
│       ├── snake.py
│       └── pick_ledger.py
├── data/
│   ├── raw/
│   │   ├── yahoo/
│   │   └── hoopshype/
│   ├── normalized/
│   └── fixtures/
├── reports/
│   ├── yahoo-api-findings.md
│   ├── hoopshype-findings.md
│   ├── player-matching-results.csv
│   ├── draft-pick-trade-findings.md
│   └── phase-0-summary.md
└── tests/
    ├── test_name_normalization.py
    ├── test_salary_aggregation.py
    ├── test_player_matching.py
    ├── test_lottery_order.py
    └── test_pick_ownership.py
```

Do not commit OAuth tokens, cookies, Yahoo secrets, or authenticated page content containing personal information.

---

# 4. Workstream A: Yahoo API access

## Objective

Establish authenticated access to the user’s Yahoo Fantasy account and retrieve the available NBA fantasy leagues.

## Tasks

### A1. Register the Yahoo application

Create a Yahoo developer application and record:

* Client ID
* Client secret
* Redirect URI
* Required OAuth scopes
* Application approval status
* Any rate limits or usage restrictions

Use environment variables:

```env
YAHOO_CLIENT_ID=
YAHOO_CLIENT_SECRET=
YAHOO_REDIRECT_URI=http://localhost:8000/auth/yahoo/callback
```

### A2. Implement OAuth

Create a minimal local OAuth flow:

```text
Open authorization URL
        ↓
User authorizes Yahoo access
        ↓
Yahoo redirects to local callback
        ↓
Application exchanges code for tokens
        ↓
Tokens are stored locally and securely
```

Test:

* Initial authorization
* Access-token use
* Access-token expiration
* Refresh-token flow
* Revoked or invalid credentials
* Missing permissions

### A3. List available leagues

Retrieve the user’s available fantasy basketball leagues.

Capture:

* Yahoo game key
* League key
* League name
* Season
* Number of teams
* Scoring type
* Draft type
* League status

## Deliverables

* Working Yahoo OAuth script
* Saved redacted league-list response
* Documented Yahoo identifiers
* Refresh-token test result

## Exit criteria

* Authentication succeeds without manually copying temporary tokens.
* At least one NBA fantasy league can be identified.
* The selected league can be retrieved by its Yahoo league key.
* Token refresh works after access-token expiration.

---

# 5. Workstream B: League, standings, and roster extraction

## Objective

Confirm that Yahoo contains enough information to build the keeper eligibility roster and determine the final fantasy standings.

## Tasks

### B1. Retrieve league settings

Capture relevant settings such as:

* Team count
* Roster positions
* Season dates
* Scoring format
* Draft type
* Keeper configuration, if exposed
* Transaction deadline
* Playoff settings

### B2. Retrieve teams

For every team, capture:

* Yahoo team key
* Team name
* Manager name or nickname
* Team logo, where available
* Division, where applicable
* Final rank
* Playoff seed
* Wins, losses, and ties
* Points or category standings

### B3. Retrieve rosters

For every rostered player, capture:

* Yahoo player key
* Player name
* NBA team
* Eligible positions
* Selected roster position
* Status
* Injury designation
* Acquisition type, if available

Test both:

* Current roster
* A roster for a specified historical date or scoring week, if Yahoo supports it

### B4. Determine the keeper snapshot rule

Decide which roster should be used for keeper eligibility.

Recommended options to test:

1. Final roster on the last day of the fantasy season
2. Roster at the end of the championship matchup
3. Current roster when the commissioner begins the offseason
4. Manually selected snapshot date

The preferred implementation is an immutable roster snapshot saved by the application.

## Deliverables

* One complete league JSON fixture
* Team and standings CSV
* Complete roster CSV
* Recommended keeper-snapshot rule
* List of fields missing from Yahoo

## Exit criteria

* All 12 fantasy teams are retrieved.
* Every rostered player has a Yahoo player key.
* Final standings can be ordered from champion to last place.
* The bottom nine and top three teams can be identified unambiguously.
* A keeper-eligibility snapshot can be stored without depending on later Yahoo changes.

---

# 6. Workstream C: Yahoo draft-pick trade investigation

## Objective

Determine whether Yahoo’s API exposes sufficient information to reconstruct ownership of draft picks traded between fantasy teams.

This is the highest-risk Yahoo workstream.

## Tasks

### C1. Retrieve league transactions

Retrieve all available transaction types:

* Adds
* Drops
* Add/drop combinations
* Player trades
* Commissioner changes
* Draft-pick trades
* Vetoed or rejected trades
* Pending and completed trades

Store the complete raw response before parsing.

### C2. Identify known draft-pick trades

Choose at least one transaction that is known to contain a traded draft pick.

For each traded pick, try to identify:

* Draft season
* Round
* Original fantasy team
* Team trading the pick
* Team receiving the pick
* Yahoo transaction ID
* Transaction timestamp
* Transaction status

### C3. Inspect alternative Yahoo endpoints

Check whether pick information appears in:

* League transaction collection
* Individual transaction resource
* Team transaction history
* Draft results
* League settings
* Yahoo trade-review pages

### C4. Compare the API with the Yahoo page

For the same known transaction:

1. Save the API response.
2. Open the corresponding Yahoo league page.
3. Record exactly what draft-pick information is visible.
4. Compare the visible page data with the API data.

### C5. Test authenticated page extraction

Only when the API does not provide sufficient information, create a small Playwright proof of concept.

The proof of concept should:

* Use a user-controlled authenticated browser session
* Open the transaction history page
* Identify trade rows
* Extract the visible trade description
* Extract round and team information
* Save a redacted HTML fixture
* Avoid storing the Yahoo password

### C6. Design the manual fallback

Define a CSV format:

```csv
draft_season,round,original_team,from_team,to_team,trade_date,yahoo_transaction_id,notes
2027,1,Team A,Team A,Team B,2026-02-12,123456789,Imported from Yahoo trade page
```

Also define a manual-entry payload:

```json
{
  "draft_season": 2027,
  "round": 1,
  "original_team": "Team A",
  "from_team": "Team A",
  "to_team": "Team B",
  "trade_date": "2026-02-12",
  "source": "manual",
  "notes": "Verified against Yahoo transaction page"
}
```

## Deliverables

* Raw Yahoo transaction fixture
* Parsed transaction report
* API-versus-page comparison
* Page-extraction proof of concept, when necessary
* Manual CSV specification
* Recommendation for Phase 5

## Exit criteria

At least one of these approaches must work:

1. Draft-pick trades can be completely imported from the Yahoo API.
2. Draft-pick trades can be extracted from an authenticated Yahoo page.
3. Draft-pick trades can be entered through a reliable manual or CSV workflow.

The dashboard must not depend entirely on page scraping.

---

# 7. Workstream D: Hoopshype salary extraction

## Objective

Confirm that Hoopshype salary data can be imported and that players with multiple applicable contracts can be aggregated correctly.

## Tasks

### D1. Inspect the source page

Determine:

* Whether the table is present in the initial HTML
* Whether JavaScript rendering is required
* Whether pagination or lazy loading exists
* Whether there are multiple season columns
* How player links and team names are represented
* How multiple contracts are represented
* Whether duplicate rows appear

### D2. Build the simplest viable importer

Try import methods in this order:

1. Standard HTTP request plus HTML parser
2. `pandas.read_html`
3. Browser rendering through Playwright
4. Commissioner-provided CSV import

The importer must retain the raw source rows before aggregation.

### D3. Define extracted fields

For each source row, capture:

```text
Source player name
Player page URL or source identifier
NBA team
Salary season
Salary amount
Contract or row description
Import timestamp
Source-row fingerprint
```

### D4. Test salary parsing

Handle salary values such as:

```text
$12,500,000
$850,000
-
N/A
```

Convert valid salary amounts to integer cents or whole dollars.

Recommended storage:

```text
12500000
```

Do not store salary values as floating-point numbers.

### D5. Investigate multiple contracts

Find actual examples of players appearing with multiple salary entries.

Determine whether entries represent:

* Contracts with multiple teams
* Partial-season amounts
* Dead money
* Waived-player salary
* Two-way contracts
* Duplicate presentation rows
* Salary paid by multiple teams

Define the initial calculation:

```text
total player salary =
sum of distinct applicable salary rows
for the selected salary season
```

The application should preserve the individual rows so the commissioner can review the total.

### D6. Add duplicate protection

Generate a stable row fingerprint using fields such as:

```text
season
normalized player name
team
salary
contract description
```

Repeated imports must not multiply salaries.

## Deliverables

* Working salary import script
* Raw Hoopshype fixture
* Normalized salary CSV
* Multiple-contract examples
* Duplicate-handling rules
* Documented fallback import format

## Exit criteria

* Salary data can be imported for the target season.
* Each raw source row is preserved.
* Salary values are parsed into integers.
* Duplicate imports do not increase totals.
* At least one multiple-contract player is tested.
* A manual CSV fallback exists.

---

# 8. Workstream E: Player identity matching

## Objective

Measure how accurately Yahoo players can be linked with Hoopshype salary records.

## Tasks

### E1. Create a normalized-name function

Normalize names by:

* Applying Unicode normalization
* Removing diacritics
* Converting to lowercase
* Removing periods and unnecessary punctuation
* Normalizing apostrophes and hyphens
* Collapsing whitespace
* Standardizing suffixes
* Preserving meaningful name components

Examples:

```text
Nikola Jokić       → nikola jokic
P.J. Washington    → pj washington
Gary Trent Jr.     → gary trent jr
Robert Williams III → robert williams iii
```

### E2. Add suffix handling

Standardize:

```text
Junior → jr
Jr.    → jr
Senior → sr
II     → ii
III    → iii
IV     → iv
```

Test whether matching should also compare a version with suffixes removed. Suffix removal should not automatically override an ambiguous result.

### E3. Implement matching tiers

Use this hierarchy:

1. Exact source identity already confirmed
2. Exact alias match
3. Exact normalized-name match
4. Exact name plus NBA-team agreement
5. Fuzzy name plus team agreement
6. Fuzzy name plus position agreement
7. Manual review

### E4. Produce a review report

For each Yahoo player, output:

```text
Yahoo player
Yahoo team
Suggested Hoopshype player
Hoopshype team
Salary
Match method
Confidence
Review status
```

Suggested statuses:

* Auto-matched
* Review recommended
* Ambiguous
* No Hoopshype result
* Multiple Hoopshype candidates
* Manually confirmed

### E5. Create an alias fixture

Example:

```json
[
  {
    "canonical_name": "gary trent jr",
    "aliases": [
      "gary trent junior",
      "gary trent jr."
    ]
  }
]
```

The Phase 0 alias file is only a test fixture. In the full application, confirmed aliases should be stored in the database.

### E6. Measure results

Report:

* Total Yahoo rostered players
* Exact normalized matches
* Alias matches
* Fuzzy suggested matches
* Unmatched players
* Ambiguous players
* Incorrect automatic matches discovered during review

## Deliverables

* Name-normalization module
* Matching script
* Matching-results CSV
* Alias fixture
* Accuracy report
* List of known edge cases

## Exit criteria

* Every Yahoo player receives a match status.
* No ambiguous fuzzy match is silently accepted.
* Confirmed aliases can be reused.
* The automatic match rate is measured.
* Incorrect matches can be identified and corrected.
* Salary totals are attached only to confirmed or sufficiently reliable matches.

A reasonable target is at least 95% automatic matching for active rostered players, but correctness is more important than reaching the target.

---

# 9. Workstream F: Draft lottery proof of concept

## Objective

Prove the complete first-round lottery rule using fixed test data.

## League rule

For a 12-team fantasy league:

* Bottom nine teams participate in the lottery.
* Top three teams are excluded.
* Third place receives pick 10.
* Second place receives pick 11.
* Champion receives pick 12.
* Each bottom-nine fantasy team is mapped to one of the nine worst NBA teams before the NBA lottery.
* Fantasy picks 1–9 are reordered based on the final lottery positions of the mapped NBA teams.
* Only round one is lottery-adjusted.
* Rounds 2–6 use the normal standings-based snake order.

## Tasks

### F1. Create standings fixtures

Example:

```text
1. Team A — champion
2. Team B
3. Team C
4. Team D
5. Team E
6. Team F
7. Team G
8. Team H
9. Team I
10. Team J
11. Team K
12. Team L — worst
```

### F2. Create the pre-lottery mapping

```text
Team L → worst NBA team
Team K → second-worst NBA team
Team J → third-worst NBA team
Team I → fourth-worst NBA team
Team H → fifth-worst NBA team
Team G → sixth-worst NBA team
Team F → seventh-worst NBA team
Team E → eighth-worst NBA team
Team D → ninth-worst NBA team
```

### F3. Create final NBA lottery results

Use synthetic test results in which teams move both upward and downward.

### F4. Generate first-round order

Algorithm:

```python
lottery_entries = [
    fantasy teams ranked 4th through 12th
]

lottery_entries.sort(
    key=lambda entry: entry.mapped_nba_final_lottery_position
)

round_one = lottery_entries + [
    third_place_team,
    second_place_team,
    champion,
]
```

### F5. Generate rounds 2–6

```text
Round 2: champion to worst
Round 3: worst to champion
Round 4: champion to worst
Round 5: worst to champion
Round 6: champion to worst
```

### F6. Test edge cases

Test:

* Two mappings accidentally assigned to the same NBA team
* A fantasy team without an NBA mapping
* Missing NBA lottery result
* NBA lottery result outside the mapped bottom nine
* Top-three fantasy team accidentally included
* More or fewer than 12 fantasy teams
* Traded first-round pick
* Pick traded more than once

## Deliverables

* Draft-generation script
* Synthetic standings fixture
* Synthetic NBA lottery fixture
* Generated six-round draft CSV
* Unit tests for the league rules

## Exit criteria

* The first round contains exactly 12 picks.
* Picks 1–9 belong to the lottery-eligible original teams.
* Pick 10 belongs to the third-place original team.
* Pick 11 belongs to the second-place original team.
* Pick 12 belongs to the champion’s original team.
* Rounds 2–6 follow the standings-based snake.
* The draft contains exactly 72 picks.
* Every original fantasy team has exactly one pick per round before trades.

---

# 10. Workstream G: Draft-pick ownership ledger

## Objective

Prove that draft positions and draft-pick ownership can be managed independently.

## Core rule

A pick’s position is determined by its original team.

A pick’s current user is determined by its ownership history.

Example:

```text
Original pick: Team L, Round 1
Lottery result: Pick 3
Trade history:
    Team L → Team F
    Team F → Team B

Final result:
    Pick 3 is owned by Team B
```

## Tasks

### G1. Create original picks

Generate one pick for each combination of:

```text
12 original teams × 6 rounds = 72 picks
```

### G2. Apply ownership events

Each event should include:

```text
Draft season
Round
Original team
From team
To team
Transaction date
Source
External transaction ID
Notes
```

### G3. Replay events

Sort events chronologically and update ownership.

Validation:

* The `from_team` must own the pick at that point.
* The same event must not be imported twice.
* The round must be between 1 and 6.
* The original team must not change.
* A pick must have exactly one current owner.

### G4. Test repeated trades

Test:

```text
Team L → Team F
Team F → Team B
Team B → Team H
```

### G5. Test lottery interaction

Confirm that a traded first-round pick moves according to Team L’s mapped NBA lottery outcome, even when Team H owns it.

## Deliverables

* Ownership-ledger implementation
* Trade fixtures
* Validation tests
* Final pick-ownership CSV

## Exit criteria

* Every pick has one original team.
* Every pick has one current owner.
* Replaying the same transaction import is idempotent.
* Invalid ownership chains are rejected or flagged.
* Lottery movement changes pick position but not ownership history.

---

# 11. Sample Phase 0 data outputs

## Normalized roster output

```csv
yahoo_team_key,team_name,yahoo_player_key,player_name,nba_team,position
...
```

## Salary output

```csv
source_player_name,normalized_name,nba_team,season,salary,source_row_fingerprint
...
```

## Matching output

```csv
yahoo_player_key,yahoo_name,hoopshype_name,confidence,status,total_salary
...
```

## Draft output

```csv
round,slot,overall_pick,original_team,current_owner,lottery_mapping
1,1,1,Team J,Team J,NBA Team X
...
```

## Pick-trade output

```csv
draft_season,round,original_team,from_team,to_team,transaction_date,source
...
```

---

# 12. Tests required in Phase 0

At minimum, create tests for:

## Yahoo parsing

* All teams are parsed.
* Standings ranks are unique.
* Player keys are retained.
* Missing optional fields do not crash the importer.

## Salary parsing

* Currency symbols and commas are removed correctly.
* Empty salaries become null rather than zero.
* Duplicate source rows are ignored.
* Multiple distinct contract rows are added correctly.

## Player matching

* Diacritics are normalized.
* Suffixes are standardized.
* Periods and apostrophes are handled.
* Team mismatches reduce confidence.
* Ambiguous names require review.

## Draft generation

* Exactly nine teams enter the lottery.
* Top three teams receive picks 10–12.
* Exactly 72 picks are generated.
* Later rounds use the correct direction.
* Each team receives one original pick per round.

## Pick ownership

* A valid trade changes current ownership.
* An invalid sender is rejected.
* Multiple trades replay in chronological order.
* Duplicate events are idempotent.
* Lottery position remains tied to the original team.

---

# 13. Risks and fallback decisions

| Risk                                | Phase 0 response                                             | Full-system fallback                  |
| ----------------------------------- | ------------------------------------------------------------ | ------------------------------------- |
| Yahoo API approval is unavailable   | Document blocker and test with saved fixtures where possible | Commissioner CSV import               |
| Yahoo omits draft-pick trades       | Inspect authenticated Yahoo page                             | Page import plus manual ledger        |
| Yahoo page structure changes        | Keep parser isolated and fixture-tested                      | CSV/manual entry                      |
| Hoopshype blocks automated requests | Test browser rendering                                       | Commissioner salary CSV               |
| Hoopshype changes columns           | Parse by headings, not fixed column positions                | Import mapping screen                 |
| Multiple contracts are ambiguous    | Preserve every raw row                                       | Commissioner include/exclude override |
| Player names do not match           | Use team, position, aliases, and review                      | Permanent manual identity mapping     |
| Final standings are ambiguous       | Compare rank, playoff finish, and league rules               | Commissioner standings override       |
| NBA mapping is incomplete           | Block draft finalization                                     | Manual mapping screen                 |

---

# 14. Phase 0 decision log

Maintain a decision log in `reports/phase-0-summary.md`.

Each decision should include:

```text
Decision
Reason
Evidence
Alternatives considered
Effect on later phases
Manual fallback
```

Required decisions:

1. Yahoo library or direct HTTP client
2. Token-storage approach
3. Keeper roster snapshot date
4. Yahoo draft-pick trade import method
5. Hoopshype import method
6. Salary season used for keepers
7. Treatment of multiple contracts
8. Player-match confidence thresholds
9. Lottery mapping source
10. Manual override policy

---

# 15. Final Phase 0 deliverables

Phase 0 is complete when the repository contains:

* Working Yahoo OAuth flow
* League-selection proof of concept
* Full team and roster extraction
* Final standings extraction
* Yahoo transaction investigation
* Known draft-pick trade comparison
* Hoopshype salary importer or documented fallback
* Multiple-contract salary test
* Player reconciliation report
* Six-round draft generator
* Bottom-nine lottery implementation
* Top-three fixed-pick implementation
* Draft-pick ownership ledger
* Automated tests
* Redacted raw fixtures
* Phase 0 findings report
* Go/no-go recommendation for each integration

---

# 16. Go/no-go checklist

## Yahoo league integration

* [ ] OAuth works
* [ ] Token refresh works
* [ ] Leagues can be listed
* [ ] Selected league can be loaded
* [ ] All teams are retrieved
* [ ] Final standings are available
* [ ] Rosters are complete

## Yahoo pick trades

* [ ] Known trade found in API, or
* [ ] Known trade extracted from Yahoo page, or
* [ ] Manual CSV workflow validated

## Hoopshype

* [ ] Salary rows can be imported
* [ ] Selected season is available
* [ ] Salary values parse correctly
* [ ] Multiple-contract example verified
* [ ] Duplicate imports are safe
* [ ] CSV fallback is documented

## Player matching

* [ ] Normalization implemented
* [ ] Match confidence recorded
* [ ] Ambiguous results are reviewed
* [ ] Confirmed aliases can be saved
* [ ] Automatic match rate measured

## Draft engine

* [ ] Bottom nine enter lottery
* [ ] Top three are excluded
* [ ] Third place receives pick 10
* [ ] Second place receives pick 11
* [ ] Champion receives pick 12
* [ ] Rounds 2–6 snake correctly
* [ ] Exactly 72 original picks are generated
* [ ] Traded picks retain original-team positioning

---

# 17. Recommended execution order

Execute Phase 0 in this order:

```text
1. Yahoo application and OAuth
2. League, team, standings, and roster extraction
3. Yahoo draft-pick trade investigation
4. Hoopshype salary extraction
5. Multiple-contract analysis
6. Yahoo-to-Hoopshype player matching
7. Lottery and six-round draft proof of concept
8. Draft-pick ownership ledger
9. Automated tests
10. Phase 0 findings and architecture decisions
```

The Yahoo draft-pick trade investigation should happen early because it determines whether the future dashboard needs an API importer, authenticated page importer, or primarily manual trade ledger.
