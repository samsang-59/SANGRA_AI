-- 001: the 9 tables of 03_database_design.md, with their checks and indexes.
-- Never edit this file after it was applied: the migration command stores its checksum and refuses a changed file.

CREATE TABLE users (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                text        NOT NULL,
    email               text        NOT NULL UNIQUE CHECK (email = lower(email)),
    password_hash       text        NOT NULL,
    is_email_verified   boolean     NOT NULL DEFAULT false,
    password_changed_at timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE user_tokens (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_type  text        NOT NULL CHECK (token_type IN ('email_verification', 'password_reset')),
    token_hash  text        NOT NULL UNIQUE,
    expires_at  timestamptz NOT NULL,
    used_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE projects (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id          bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name             text        NOT NULL,
    language         text        NOT NULL CHECK (language IN ('java', 'python')),
    source_type      text        NOT NULL CHECK (source_type IN ('github', 'local_path', 'zip')),
    source_reference text,
    branch           text,
    test_command     text        NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    -- source_reference must be filled unless source_type is 'zip'
    CONSTRAINT projects_reference_required
        CHECK (source_type = 'zip' OR (source_reference IS NOT NULL AND source_reference <> ''))
);
CREATE INDEX projects_user_idx ON projects (user_id);

CREATE TABLE runs (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id              bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    project_id           bigint      NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    bug_description      text        NOT NULL,
    status               text        NOT NULL CHECK (status IN (
                             'started', 'searching', 'waiting_for_hint', 'waiting_for_approval',
                             'applying', 'testing', 'succeeded', 'failed', 'stopped')),
    -- snapshots of the project settings used by this run
    language             text        NOT NULL CHECK (language IN ('java', 'python')),
    test_command         text        NOT NULL,
    branch               text,
    source_type          text        NOT NULL CHECK (source_type IN ('github', 'local_path', 'zip')),
    source_reference     text,
    source_commit        text,
    llm_model            text        NOT NULL,
    workspace_path       text,
    workspace_deleted_at timestamptz,
    stop_reason          text,
    reject_count         integer     NOT NULL DEFAULT 0 CHECK (reject_count >= 0),
    failed_test_count    integer     NOT NULL DEFAULT 0 CHECK (failed_test_count >= 0),
    hint_count           integer     NOT NULL DEFAULT 0 CHECK (hint_count >= 0),
    final_summary        text,
    total_tokens         integer,
    applied_to_source_at timestamptz,
    started_at           timestamptz NOT NULL DEFAULT now(),
    finished_at          timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
-- ONE ACTIVE RUN PER USER: the database itself blocks a second active run.
CREATE UNIQUE INDEX runs_one_active_per_user ON runs (user_id)
    WHERE status IN ('started', 'searching', 'waiting_for_hint', 'waiting_for_approval', 'applying', 'testing');
CREATE INDEX runs_user_created_idx ON runs (user_id, created_at DESC);

CREATE TABLE attempts (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id            bigint      NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    attempt_number    integer     NOT NULL CHECK (attempt_number >= 1),
    reason_started    text        NOT NULL CHECK (reason_started IN ('first', 'after_reject', 'after_test_failure')),
    explanation       text,
    status            text        NOT NULL CHECK (status IN (
                          'proposed', 'waiting_for_approval', 'rejected', 'applied', 'tests_passed', 'tests_failed')),
    decision          text        CHECK (decision IN ('accepted', 'rejected')),
    decision_feedback text,
    decided_at        timestamptz,
    applied_at        timestamptz,
    reverted_at       timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, attempt_number)
);

CREATE TABLE file_edits (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    attempt_id  bigint      NOT NULL REFERENCES attempts (id) ON DELETE CASCADE,
    file_path   text        NOT NULL,
    start_line  integer     NOT NULL,
    end_line    integer     NOT NULL,
    old_code    text        NOT NULL,
    new_code    text        NOT NULL,  -- empty string means delete
    edit_order  integer     NOT NULL,
    explanation text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT file_edits_line_range CHECK (start_line >= 1 AND end_line >= start_line),
    UNIQUE (attempt_id, edit_order)
);

CREATE TABLE run_events (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id          bigint      NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    attempt_id      bigint      REFERENCES attempts (id) ON DELETE CASCADE,
    sequence_number integer     NOT NULL CHECK (sequence_number >= 1),
    event_type      text        NOT NULL CHECK (event_type IN (
                        'status_changed', 'tool_called', 'tool_result', 'hint_requested', 'hint_received',
                        'approval_requested', 'approval_decided', 'tests_started', 'tests_finished', 'error')),
    message         text        NOT NULL,
    details         jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, sequence_number)
);

CREATE TABLE test_results (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id       bigint      NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    attempt_id   bigint      UNIQUE REFERENCES attempts (id) ON DELETE CASCADE,
    test_type    text        NOT NULL CHECK (test_type IN ('baseline', 'after_fix')),
    status       text        NOT NULL CHECK (status IN ('passed', 'failed', 'error', 'timed_out')),
    exit_code    integer,
    log_excerpt  text,
    duration_ms  integer     CHECK (duration_ms >= 0),
    docker_image text,
    started_at   timestamptz,
    finished_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    -- a baseline row has NO attempt, an after_fix row MUST have one
    CONSTRAINT test_results_attempt_matches_type CHECK (
        (test_type = 'baseline' AND attempt_id IS NULL) OR (test_type = 'after_fix' AND attempt_id IS NOT NULL))
);
-- only one baseline per run
CREATE UNIQUE INDEX test_results_one_baseline_per_run ON test_results (run_id) WHERE test_type = 'baseline';

CREATE TABLE packages (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id       bigint      NOT NULL UNIQUE REFERENCES runs (id) ON DELETE CASCADE,
    storage_path text        NOT NULL,
    size_bytes   bigint      NOT NULL CHECK (size_bytes >= 0),
    expires_at   timestamptz NOT NULL,
    deleted_at   timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT packages_expires_after_created CHECK (expires_at > created_at)
);
CREATE INDEX packages_expires_idx ON packages (expires_at) WHERE deleted_at IS NULL;
