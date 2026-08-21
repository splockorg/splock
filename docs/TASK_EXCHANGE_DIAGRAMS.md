# Task exchange — diagrams

Companion to [`docs/TASK_EXCHANGE.md`](TASK_EXCHANGE.md), which carries the
prose, the invariants, and the compliance boundary. This file is the picture.
All diagrams are mermaid and render natively on GitHub.

**Status:** concept. Nothing here is implemented.

---

## 1. Topology — what runs where

The central component is a **table**, not a service. It holds text. It never
holds a credential and never makes a model call. Every model call happens inside
an operator's own boundary, on that operator's own account.

```mermaid
flowchart LR
    subgraph OPA["Operator A — own machine, own account"]
        A_FLEET["bin/fleet<br/>board / spawn"]
        A_CRON["exchange cron<br/>publish + heartbeat"]
        A_CC["claude -p<br/>subscription A"]
        A_FLEET --> A_CRON
        A_FLEET --> A_CC
    end

    subgraph OPB["Operator B — own machine, own account"]
        B_CRON["exchange cron<br/>match + claim + sweep"]
        B_POL["local policy<br/>opt-in, caps, deny-list"]
        B_CC["claude -p<br/>subscription B"]
        B_CRON --> B_POL --> B_CC
    end

    subgraph OPC["Operator C — own machine, own account"]
        C_CRON["exchange cron"]
        C_CC["claude -p<br/>subscription C"]
        C_CRON --> C_CC
    end

    DB[("exchange tables<br/>MySQL — text only<br/>zero credentials")]
    GIT[("shared git remote<br/>refs in, refs out")]

    A_CRON <-->|"offers, heartbeats"| DB
    B_CRON <-->|"claims, leases"| DB
    C_CRON <-->|"heartbeats"| DB

    A_FLEET -->|"push base_ref"| GIT
    B_CC -->|"push result branch"| GIT
    GIT -->|"fetch base_ref"| B_CC
    GIT -->|"PR for review"| A_FLEET

    ANT["Anthropic"]
    A_CC -->|"OAuth A"| ANT
    B_CC -->|"OAuth B"| ANT
    C_CC -->|"OAuth C"| ANT

    classDef nocred fill:#eef7ee,stroke:#3a7,stroke-width:2px
    classDef vendor fill:#f5f0ff,stroke:#85f,stroke-width:1px
    class DB,GIT nocred
    class ANT vendor
```

The three arrows into Anthropic never cross an operator boundary. That is the
whole compliance argument in one picture: **N humans, N accounts, N machines,
each spending only their own pool on work their own policy accepted.**

---

## 2. Data model

Five tables on the connection splock's intent registry already uses. Note what
is absent: there is no column anywhere for a token, a key, a cookie, or a
session. There is nowhere to put one.

```mermaid
erDiagram
    EXCHANGE_WORKERS ||--o{ EXCHANGE_CLAIMS : "executes"
    EXCHANGE_WORKERS ||--o{ EXCHANGE_OFFERS : "publishes"
    EXCHANGE_OFFERS  ||--o| EXCHANGE_CLAIMS : "has at most one"
    EXCHANGE_CLAIMS  ||--|| EXCHANGE_LEASES : "held by"
    EXCHANGE_OFFERS  ||--o{ EXCHANGE_EVENT_LOG : "audited by"
    EXCHANGE_WORKERS ||--o{ EXCHANGE_EVENT_LOG : "audited by"

    EXCHANGE_WORKERS {
        string  worker_id  PK
        string  operator   "human identity, not a credential"
        string  host
        json    models     "entitlement — what this account can spawn"
        json    stages     "local policy — what this human permits"
        json    repos      "which checkouts exist here"
        int     headroom_bucket "quantised estimate"
        string  headroom_as_of
        int     max_concurrent
        int     borrowed_today
        int     daily_ceiling
        int     fairness_debt
        string  last_heartbeat_at
        string  status     "active, paused, drained"
    }

    EXCHANGE_OFFERS {
        string  offer_id   PK
        string  publisher_worker_id FK
        string  repo
        string  base_ref
        string  slug
        string  stage
        string  required_model
        string  min_effort
        text    directive  "untrusted — lowest-trust wrap on arrival"
        string  visibility "team scope"
        int     attempt
        int     max_attempts
        string  deadline_at
        string  state
        string  created_at
    }

    EXCHANGE_CLAIMS {
        string  claim_id   PK
        string  offer_id   UK "UNIQUE — this is the mutual exclusion"
        string  worker_id  FK
        json    sort_key   "why this worker won, replayable"
        string  claimed_at
    }

    EXCHANGE_LEASES {
        string  claim_id   PK
        string  expires_at
        string  last_heartbeat_at
        string  state "held, expired, released"
    }

    EXCHANGE_EVENT_LOG {
        int     event_pk   PK
        string  event
        string  offer_id
        string  worker_id
        json    payload
        string  emitted_by
        string  host
        string  emitted_at
    }
```

---

## 3. Offer lifecycle

Every transition out of `open` is a database write with no model call behind it.
The first token is spent on entry to `executing`, and only there.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> open : published — stall, or operator ask

    open --> claimed : matcher wins the unique key
    open --> expired : deadline passed, nobody eligible
    open --> withdrawn : publisher withdraws

    claimed --> executing : worker starts claude -p
    claimed --> open : lease expired before start, attempt+1
    claimed --> cancelled : publisher cancels, worker honours at heartbeat

    executing --> returned : branch pushed, envelope written
    executing --> open : worker died, lease swept, attempt+1
    executing --> rate_limited : worker hit its own window
    executing --> refused : local policy blocked the stage

    rate_limited --> open : headroom band lowered, attempt+1
    refused --> open : that worker filtered out, attempt+1

    open --> failed : attempt exceeds max_attempts

    returned --> [*] : publisher reviews the PR
    expired --> [*]
    failed --> [*]
    withdrawn --> [*]
    cancelled --> [*]

    note right of open
        Zero tokens spent
        anywhere left of
        "executing".
    end note
```

---

## 4. End-to-end sequence

The motivating case: operator A's `/code` stage needs Fable and A's pool is dry
or A has no Fable entitlement.

```mermaid
sequenceDiagram
    autonumber
    actor A as Operator A
    participant AF as A · bin/fleet
    participant DB as exchange tables
    participant BC as B · matcher cron
    participant BP as B · local policy
    participant BX as B · claude -p
    participant G as git remote

    A->>AF: /code on a stalled slug
    AF-->>A: blocked — needs claude-fable-5, pool dry
    AF->>G: push base_ref
    AF->>DB: INSERT offer (stage, required_model, base_ref, directive)
    Note over DB: state = open · zero tokens spent

    loop every worker, every minute
        BC->>DB: UPDATE heartbeat + headroom_bucket (from local ledger)
    end

    BC->>DB: SELECT open offers + eligible workers
    Note over BC: filter, then sort:<br/>headroom · fairness · concurrency · blake2b tiebreak<br/>deterministic — same snapshot, same winner everywhere
    BC->>DB: INSERT claim (UNIQUE offer_id)
    DB-->>BC: 1 row — B won
    BC->>DB: INSERT lease (expires_at)

    BC->>BP: hand the offer to local policy
    BP->>BP: lowest-trust wrap of directive<br/>apply B's own deny-list, permission mode, caps
    alt B's policy accepts
        BP->>G: fetch base_ref into B's checkout
        BP->>BX: claude -p /splock:code SLUG — subscription B
        Note over BX: FIRST TOKEN SPENT — after assignment, not before
        loop while running
            BC->>DB: heartbeat lease
        end
        BX->>G: push result branch
        BX->>DB: UPDATE offer state = returned + envelope
        BC->>DB: fairness_debt += 1 · borrowed_today += 1
    else B's policy refuses
        BP->>DB: state = refused, requeue, B filtered next round
    end

    DB-->>AF: board shows returned + branch ref
    G-->>A: PR for review
    A->>A: review and merge — never auto-merge
```

---

## 5. The matcher — deterministic by construction

Eligibility is a filter; nothing in it can be outweighed. The sort below it has
no float, no `RANDOM()`, and no wall-clock read inside a comparison, so two
matchers on two machines against one snapshot cannot disagree.

```mermaid
flowchart TD
    START(["cron tick — zero tokens from here to the claim"]) --> SNAP["single transaction<br/>one NOW() from the DB server"]
    SNAP --> OFFERS["SELECT offers WHERE state = open<br/>ORDER BY created_at, offer_id"]
    OFFERS --> LOOP{"next offer"}
    LOOP -->|"none"| SWEEP

    LOOP -->|"offer"| F1{"worker has<br/>required_model?"}
    F1 -->|no| DROP["filtered out"]
    F1 -->|yes| F2{"worker has repo<br/>+ base_ref reachable?"}
    F2 -->|no| DROP
    F2 -->|yes| F3{"stage enabled in<br/>worker's local policy?"}
    F3 -->|no| DROP
    F3 -->|yes| F4{"stage is offerable?<br/>not in unspawnable_stages"}
    F4 -->|no| VOID["offer voided — attended only"]
    F4 -->|yes| F5{"in offer visibility?<br/>heartbeat fresh?"}
    F5 -->|no| DROP
    F5 -->|yes| F6{"borrowed_today<br/>below daily ceiling?"}
    F6 -->|no| DROP
    F6 -->|yes| POOL["eligible pool"]

    POOL --> S1["sort 1 — headroom bucket DESC<br/>quantised, never raw"]
    S1 --> S2["sort 2 — fairness debt DESC<br/>lent minus borrowed"]
    S2 --> S3["sort 3 — concurrency headroom DESC"]
    S3 --> S4["sort 4 — blake2b of offer_id+worker_id ASC<br/>fixed hash, no salt, no clock"]
    S4 --> WIN{"pool empty?"}
    WIN -->|yes| AGE["age the offer<br/>expire at deadline with the filter that emptied it"]
    WIN -->|no| CLAIM["INSERT claim ON UNIQUE offer_id"]
    CLAIM --> ONE{"affected rows = 1?"}
    ONE -->|no| LOSE["another matcher won — stop, correct outcome"]
    ONE -->|yes| LEASE["INSERT lease + log the sort_key that decided it"]
    LEASE --> LOOP
    AGE --> LOOP
    DROP --> LOOP
    VOID --> LOOP
    LOSE --> LOOP

    SWEEP["sweep expired leases<br/>requeue with attempt+1"] --> END(["done — still zero tokens"])
```

---

## 6. The compliance gate

The design's shape is not a matter of taste. Each refusal below maps to a quoted
clause in [`docs/TASK_EXCHANGE.md`](TASK_EXCHANGE.md#compliance--where-the-redline-is).

```mermaid
flowchart TD
    Q0(["a proposed exchange behaviour"]) --> Q1{"does a credential<br/>leave its own machine?"}
    Q1 -->|yes| R1["REDLINE 1 — refuse<br/>Consumer Terms §2:<br/>no sharing credentials,<br/>no making your Account<br/>available to anyone else"]
    Q1 -->|no| Q2{"does one host execute<br/>under someone else's login?"}
    Q2 -->|yes| R2["REDLINE 2 — refuse<br/>Legal and compliance:<br/>no routing requests through<br/>Pro/Max credentials on<br/>behalf of their users"]
    Q2 -->|no| Q3{"is capacity traded<br/>for money or outside<br/>the organisation?"}
    Q3 -->|yes| R3["REDLINE 3 — refuse<br/>Consumer Terms §3: no resale;<br/>credits cannot be pooled<br/>across teammates"]
    Q3 -->|no| Q4{"multiple accounts held<br/>by one human as<br/>separate workers?"}
    Q4 -->|yes| R4["REDLINE 4 — refuse<br/>AUP: capacity circumvention,<br/>coordination across accounts"]
    Q4 -->|no| Q5{"is a human accountable<br/>for every registered worker?"}
    Q5 -->|no| R5["REDLINE 5 — refuse<br/>an unattended login is<br/>redline 2 in disguise"]
    Q5 -->|yes| Q6{"continuous unattended<br/>high-volume borrowing?"}
    Q6 -->|yes| AMBER["AMBER — not a bright line<br/>'limits assume ordinary,<br/>individual usage'<br/>→ cap it, or move the shared<br/>portion to an API key,<br/>which is Anthropic's own advice"]
    Q6 -->|no| GREEN["GREEN — ship it<br/>opt-in, capped, reciprocal,<br/>visible ledger, team scope"]

    classDef bad fill:#fdecec,stroke:#c33,stroke-width:2px
    classDef warn fill:#fff7e6,stroke:#e90,stroke-width:2px
    classDef good fill:#eef7ee,stroke:#3a7,stroke-width:2px
    class R1,R2,R3,R4,R5 bad
    class AMBER warn
    class GREEN good
```

---

## 7. Where this sits beside multi-routing

Two answers to one problem — "this stage needs capability I do not have right
now." They compose: a worker that picks up an offer may itself route the stage
to another vendor.

```mermaid
flowchart LR
    STALL["stage needs a capability<br/>this operator lacks"]
    STALL --> AXIS1["task exchange<br/>— a different teammate<br/>— same vendor<br/>— 5 tables + a cron<br/>— compliance boundary is the work"]
    STALL --> AXIS2["multi-routing<br/>— a different vendor<br/>— same operator<br/>— transports + schema sanitising<br/>— capability matrix is the work"]
    AXIS1 --> BOTH["compose:<br/>teammate B picks up the offer<br/>and routes the coder to Codex,<br/>verifier stays pinned Haiku"]
    AXIS2 --> BOTH
```
