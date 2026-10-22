# SANGRA_AI - Workflow (System Architecture)

(Written in simple language. Date: 2026-10-22. Made from the design docs 01 to 04.)

This file shows the whole backend as pictures: how a request travels through the layers, where the agent sits inside the
backend, the agent's own workflow, the life of a run, the pause and resume, and how the tables link.

**How to view:** the diagrams are written in Mermaid. They show automatically on GitHub, in Obsidian, and in Typora. In VS Code,
install the extension "Markdown Preview Mermaid Support". Picture versions of every diagram are also in the `diagrams/` folder,
so you can open them in any image viewer.

## Shapes and colours

| Shape or line | Meaning |
|---|---|
| Rounded pill | Start, end, or something outside our backend (user, Groq, GitHub, email) |
| Rectangle | A part of the system, or a step of the agent |
| Hexagon | A step where the LLM thinks |
| Diamond | A decision |
| Slanted box (parallelogram, orange) | A PAUSE: the graph stops here and waits for the user |
| Cylinder | Storage (database or disk) |
| Solid arrow | A call or a flow |
| Dotted line | A connection that is only there to help (login check, live stream, mounted folder) |
| Blue | Web layers and LLM steps | 
| Green | Steps that run tests in Docker |
| Red | Ending steps |

---

## 1. The whole system, with the agent inside

Every request from the browser goes down the chain Router, Validators, Controller, Services, Repositories, and then to the database.
The agent is a separate layer inside the backend. Services start it, and its steps call the services back, so the layer rules stay
intact. LangGraph keeps its own tables in the same PostgreSQL, but our code never reads them.

```mermaid
flowchart TB
    user(["User<br/>Browser / Frontend"])

    subgraph server["Backend server - one Python process, async"]
        direction TB

        subgraph core["Core / Config"]
            direction LR
            settings["Settings<br/>.env"]
            authchk["Login check<br/>JWT"]
            rate["Rate limiter"]
            logs["Errors + logging"]
            mailer["Mailer"]
            pool["DB connection pool"]
        end

        subgraph web["Web layers - every request goes down this chain"]
            direction TB
            router["1 Router<br/>URL + public or protected"]
            validators["2 Validators<br/>shape of the request"]
            controller["3 Controller<br/>result to HTTP status + JSON"]
            service["4 Services<br/>Auth - Project - Run<br/>business rules"]
            repo["5 Repositories<br/>all SQL + transactions"]
            router --> validators --> controller --> service --> repo
        end

        subgraph agent["6 Agent layer - one background task per run"]
            direction TB
            runner["AgentRunner<br/>start - resume - cancel"]
            lgraph{{"LangGraph workflow<br/>11 steps - see diagram 3"}}
            tools["CodeTools<br/>3 read-only tools"]
            llm["LLM client"]
            runner --> lgraph
            lgraph --> tools
            lgraph --> llm
        end

        subgraph helpers["Helpers"]
            direction TB
            ws["WorkspaceManager<br/>copy + git + path guard"]
            tr["TestRunner"]
            fin["Finalizer<br/>package + finish"]
        end

        jobs["Background jobs<br/>1-hour timeout - cleanup - startup recovery"]
    end

    db[("PostgreSQL<br/>our tables + LangGraph tables")]
    files[("Disk<br/>workspaces/ and packages/")]
    docker["Docker engine<br/>test containers"]
    groq(["Groq<br/>the LLM"])
    gh(["GitHub<br/>public repos"])
    mail(["Email provider<br/>or console"])

    user -->|"HTTPS + Bearer JWT"| router
    controller -.->|"live stream SSE"| user
    router -.- authchk
    router -.- rate
    repo --> db
    pool -.- repo
    service -->|"start / resume / cancel"| runner
    lgraph -->|"calls services"| service
    service --> mailer --> mail
    llm --> groq
    lgraph --> ws
    lgraph --> tr
    lgraph --> fin
    ws --> files
    ws --> gh
    tr --> docker
    docker -.->|"mounts code/"| files
    fin --> files
    runner -->|"LangGraph saves its state"| db
    jobs --> service

    classDef ext fill:#f1f5f9,stroke:#64748b,color:#0f172a
    classDef store fill:#fef9c3,stroke:#ca8a04,color:#0f172a
    classDef layer fill:#dbeafe,stroke:#2563eb,color:#0f172a
    classDef ai fill:#ede9fe,stroke:#7c3aed,color:#0f172a
    class user,docker,groq,gh,mail ext
    class db,files store
    class router,validators,controller,service,repo layer
    class runner,lgraph,tools,llm ai
```

## 2. One request through the layers (start a run)

The agent is started in the background, so the request answers at once. The live screen then follows the run through a stream.

```mermaid
sequenceDiagram
    autonumber
    actor U as User (browser)
    participant R as Router
    participant V as Validator
    participant C as Controller
    participant S as RunService
    participant P as Repositories
    participant D as PostgreSQL
    participant A as AgentRunner

    U->>R: POST /projects/{id}/runs with Bearer JWT
    R->>R: login check (JWT, password_changed_at) and rate limit
    R->>V: check the shape of the request
    V->>C: valid request + current user
    C->>S: start_run(user, project_id, bug, zip)
    S->>P: find project by id AND user
    P->>D: SELECT
    D-->>P: row
    P-->>S: project
    S->>P: create_run (with snapshots of project settings)
    P->>D: INSERT (one-active-run rule is enforced by the database)
    D-->>P: new row, or rule violation
    P-->>S: run, or None
    S->>A: start(run_id) in the background
    S-->>C: Result (success + run) or Result (error code)
    C-->>U: 201 data=run, or 409 error=ACTIVE_RUN_EXISTS
    Note over A: the agent keeps working in the background
    U->>C: GET /runs/{id}/events (Bearer JWT, opened with fetch)
    C-->>U: live events as they happen (SSE)
```

## 3. The agent workflow (the graph)

This is the agent system architecture: 11 steps, with the two pauses (orange) and the limits (3 rejects, 3 failed test tries,
3 hints, 3 failed proposal checks, 15 tool calls per investigation). Every arrow that ends at "finish" ends the run.

```mermaid
flowchart TD
    start([Run starts])
    fin["11 finish<br/>package - final status - delete copy<br/>LangGraph snapshots deleted afterwards"]
    stop([Run ended])

    prep["1 prepare_workspace<br/>copy the code, git init if needed"]
    base["2 run_baseline_tests<br/>in Docker"]
    inv{{"3 investigate<br/>LLM + 3 read-only tools<br/>max 15 tool calls per pass"}}
    reqh["4a request_hint<br/>hint_count + 1, status waiting_for_hint"]
    waith[/"4b wait_for_hint<br/>PAUSE"/]
    prop{{"5 propose_fix<br/>LLM, fixed answer format"}}
    chk["6 check_proposal<br/>old_code matches? at most 10 edits, 300 lines?<br/>saves the attempt"]
    reqa["7a request_approval<br/>status waiting_for_approval"]
    waita[/"7b wait_for_approval<br/>PAUSE"/]
    dec{"Decision<br/>read from the database"}
    apply["8 apply_edits<br/>all or nothing"]
    tests["9 run_tests_after_fix<br/>in Docker"]
    next{"10 decide_next"}

    start --> prep
    prep -->|"clone failed, folder missing, too big, bad zip"| fin
    prep --> base
    base -->|"tests could not run"| fin
    base --> inv
    inv -->|"found something"| prop
    inv -->|"nothing found"| reqh
    reqh -->|"3 hints already used"| fin
    reqh --> waith
    waith -->|"user sends a hint"| inv
    prop --> chk
    chk -->|"check failed, under 3 in a row"| prop
    chk -->|"3 failed checks"| fin
    chk -->|"ok"| reqa
    reqa --> waita
    waita --> dec
    dec -->|"rejected, under 3"| prop
    dec -->|"rejected, limit reached"| fin
    dec -->|"accepted"| apply
    apply -->|"apply error"| fin
    apply --> tests
    tests -->|"Docker could not start"| fin
    tests --> next
    next -->|"tests passed"| fin
    next -->|"failed: undo with git, under 3"| prop
    next -->|"failed, 3 reached"| fin
    fin --> stop

    classDef llm fill:#dbeafe,stroke:#2563eb,color:#0f172a
    classDef pause fill:#ffedd5,stroke:#ea580c,color:#0f172a
    classDef docker fill:#dcfce7,stroke:#16a34a,color:#0f172a
    classDef endn fill:#fee2e2,stroke:#dc2626,color:#0f172a
    classDef decide fill:#fef9c3,stroke:#ca8a04,color:#0f172a
    class inv,prop llm
    class waith,waita pause
    class base,tests docker
    class fin,stop,start endn
    class dec,next decide
```

## 4. The life of a run (its status)

A run moves between these statuses. A run is ACTIVE until it reaches succeeded, failed, or stopped. The database allows only one
active run per user. The baseline tests run while the status is still "started".

- succeeded: the tests passed.
- stopped: ended by the user's action or inaction (cancel, 1-hour timeout, 3 rejects).
- failed: everything else (limits reached, technical problems).

```mermaid
stateDiagram-v2
    [*] --> started : user starts a run
    started --> searching : copy ready, baseline tests done
    searching --> waiting_for_hint : nothing found
    waiting_for_hint --> searching : hint received
    searching --> waiting_for_approval : fix proposed and checked
    waiting_for_approval --> applying : accepted
    waiting_for_approval --> searching : rejected, under 3
    applying --> testing : edits applied
    testing --> searching : tests failed, undone, under 3
    testing --> succeeded : tests passed
    started --> failed : copy failed, tests could not run, technical error
    searching --> failed : 3 failed checks, 3 hints used
    testing --> failed : 3 failed test tries, Docker problem
    waiting_for_hint --> stopped : cancelled or 1 hour timeout
    waiting_for_approval --> stopped : cancelled or 1 hour timeout
    searching --> stopped : cancelled, or the 3rd reject
    applying --> stopped : cancelled
    testing --> stopped : cancelled
    succeeded --> [*]
    failed --> [*]
    stopped --> [*]
```

## 5. Pause and resume (the approval)

When the graph pauses, nothing is running for that run. It is like a saved game. The user's click is a normal web request: it saves
the decision in one transaction and then resumes the graph, which reads the decision from our database.

```mermaid
sequenceDiagram
    autonumber
    actor U as User (browser)
    participant C as Controller + RunService
    participant DB as Our tables
    participant AR as AgentRunner
    participant G as Graph (LangGraph)
    participant LG as LangGraph tables

    G->>DB: request_approval: run status = waiting_for_approval, add event
    G->>LG: state saved after the step
    G-->>AR: wait_for_approval pauses - the graph stops, the task ends
    DB-->>U: live stream shows the proposal (old code and new code)
    Note over G,LG: nothing is running for this run now, it is like a saved game
    U->>C: POST /runs/{id}/approval (accept or reject, attempt_id)
    C->>DB: ONE transaction: save decision, move run status, reject_count + 1 if rejected (only if still waiting for this attempt)
    C->>AR: resume(run_id)
    AR->>G: continue with thread_id = run_id
    G->>LG: load the saved state
    G->>DB: read the decision
    G->>G: accepted: apply_edits. Rejected: propose_fix. Reject limit: finish
```

## 6. The tables and how they link

Nine tables of our own. LangGraph's tables are separate and are linked to our runs only by the label thread_id, which has the
same value as the run's id (there is no foreign key between them).

```mermaid
erDiagram
    USERS ||--o{ USER_TOKENS : has
    USERS ||--o{ PROJECTS : owns
    USERS ||--o{ RUNS : starts
    PROJECTS ||--o{ RUNS : "runs on"
    RUNS ||--o{ ATTEMPTS : has
    ATTEMPTS ||--|{ FILE_EDITS : contains
    RUNS ||--o{ RUN_EVENTS : logs
    ATTEMPTS |o--o{ RUN_EVENTS : "may own"
    RUNS ||--o{ TEST_RESULTS : has
    ATTEMPTS |o--o| TEST_RESULTS : "after-fix result"
    RUNS ||--o| PACKAGES : produces

    USERS {
        bigint id PK
        text email UK
        text password_hash
        boolean is_email_verified
        timestamptz password_changed_at
    }
    USER_TOKENS {
        bigint id PK
        bigint user_id FK
        text token_type
        text token_hash UK
        timestamptz expires_at
        timestamptz used_at
    }
    PROJECTS {
        bigint id PK
        bigint user_id FK
        text name
        text language
        text source_type
        text test_command
    }
    RUNS {
        bigint id PK
        bigint user_id FK
        bigint project_id FK
        text status
        int reject_count
        int failed_test_count
        int hint_count
        timestamptz applied_to_source_at
    }
    ATTEMPTS {
        bigint id PK
        bigint run_id FK
        int attempt_number
        text status
        text decision
    }
    FILE_EDITS {
        bigint id PK
        bigint attempt_id FK
        text file_path
        int start_line
        int end_line
        text old_code
        text new_code
    }
    RUN_EVENTS {
        bigint id PK
        bigint run_id FK
        bigint attempt_id FK
        int sequence_number
        text event_type
    }
    TEST_RESULTS {
        bigint id PK
        bigint run_id FK
        bigint attempt_id FK
        text test_type
        text status
    }
    PACKAGES {
        bigint id PK
        bigint run_id FK
        text storage_path
        timestamptz expires_at
    }
```

---

## Words to remember

- Run = the whole job. Attempt = one proposed fix (one try). Event = one line in the run's diary.
- The graph = the agent's flowchart. The state = the backpack it carries (only the agentic flow, not the app flow).
- thread_id = the label LangGraph uses to find its saved backpack. It is the same number as our run id.
- Two kinds of tokens: the JWT (kept by the browser, sent in the header on every request) and the email tokens (only in the email
  link, used once, never returned to the browser).

## Related documents

1. 01_brainstorm.md
2. 02_tech_stack.md
3. 03_database_design.md
4. 04_backend_design.md (router, validators, controller, service, repository, agent, core)
