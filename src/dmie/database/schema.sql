CREATE TABLE categories (
    category_id VARCHAR PRIMARY KEY,
    parent_category VARCHAR,
    leaf_category VARCHAR NOT NULL,
    marketplace VARCHAR NOT NULL,
    created_at TIMESTAMP
);

CREATE TABLE listings (
    listing_id VARCHAR PRIMARY KEY,
    asin VARCHAR,
    category_id VARCHAR,

    title VARCHAR,
    brand VARCHAR,
    url VARCHAR,
    image_url VARCHAR,

    price DOUBLE,
    monthly_sales DOUBLE,
    monthly_revenue DOUBLE,

    rating DOUBLE,
    review_count INTEGER,

    raw_source_file VARCHAR,

    created_at TIMESTAMP
);

CREATE TABLE listing_classification (
    listing_id VARCHAR PRIMARY KEY,

    relevant BOOLEAN,
    relevance_class VARCHAR,
    confidence DOUBLE,

    product_type VARCHAR,

    reason VARCHAR,

    classifier_version VARCHAR,
    prompt_version VARCHAR,

    review_status VARCHAR,

    created_at TIMESTAMP
);

-- Product-type classification (Milestone 11 Stage 2), deliberately its own
-- table rather than reusing listing_classification's confidence/reason/
-- review_status columns. Those columns already mean "relevance decision
-- confidence/reason/status" -- evaluation.py and the relevance pipeline
-- depend on that meaning. Overloading them for a second, unrelated
-- classification (product type) would silently corrupt the relevance
-- pipeline's own data on every re-run. See docs/product_type_classification.md
-- and DECISIONS.md "M11 Stage 3".
CREATE TABLE listing_product_type_classification (
    listing_id VARCHAR PRIMARY KEY,

    taxonomy_version INTEGER,
    product_type VARCHAR,   -- one of the frozen taxonomy's ids, or 'UNCERTAIN'
    confidence DOUBLE,
    reason VARCHAR,
    classifier_method VARCHAR,  -- rules | ai | unavailable
    review_status VARCHAR,      -- auto_accepted | needs_review

    created_at TIMESTAMP
);

CREATE TABLE products (
    product_id VARCHAR PRIMARY KEY,

    category_id VARCHAR,

    product_name VARCHAR,
    product_type VARCHAR,
    -- Milestone 11 Stage 3: majority-vote confidence and disagreement flag
    -- for `product_type` specifically -- distinct from `confidence` below,
    -- which is product IDENTITY confidence (entity resolution). Conflating
    -- the two would silently change what an existing field means, which
    -- PRINCIPLES.md and the M11 Stage 3 brief both explicitly forbid. See
    -- matching/resolution.py::build_products and DECISIONS.md "M11 Stage 3".
    product_type_confidence DOUBLE,
    product_type_conflict BOOLEAN,

    -- Product Knowledge Graph, schema-only foundation (Tier 2, Milestone 4).
    -- Family sits ABOVE product_type in the hierarchy (Family -> Type ->
    -- Model -> Variant -> Listing -> Seller) -- `model` below already
    -- existed (unpopulated) and IS the hierarchy's Model level, not a
    -- separate concept -- Variant is already represented by product_listings
    -- + each category's variant_policy (config/categories.yaml), not a new
    -- column. Deliberately left NULL for every product today: this pilot's
    -- real data (11 products, 3 real types) has no natural family grouping
    -- distinct from product_type, and populating it with an invented split
    -- would fabricate structure PRINCIPLES.md forbids. Populate only once a
    -- category has real, evidenced family-level diversity -- see
    -- docs/product_knowledge_graph.md.
    product_family VARCHAR,

    brand VARCHAR,
    model VARCHAR,

    representative_image VARCHAR,

    confidence DOUBLE,

    -- Opportunity Score (Tier 2, Milestone 9) -- a single deterministic
    -- 0-100 summary of this product's already-computed, already-evidenced
    -- PRODUCT-scoped opportunity_signals rows (PRODUCT_IMPROVEMENT,
    -- BUNDLE, CUSTOMER_PAIN_POINT). NULL when every one of those signals
    -- is still insufficient_data -- never a fabricated default. See
    -- src/dmie/opportunity/scoring.py and docs/opportunity_signals.md.
    opportunity_score DOUBLE,

    created_at TIMESTAMP
);

CREATE TABLE product_listings (
    product_id VARCHAR,
    listing_id VARCHAR,

    match_method VARCHAR,
    match_confidence DOUBLE,

    is_best_listing BOOLEAN,

    review_status VARCHAR,

    PRIMARY KEY(product_id, listing_id)
);

-- Candidate-pair table (Milestone 6): every listing pair that survived
-- blocking, with the stage that ultimately decided it and the outcome.
-- product_listings holds the final resolved MATCH clusters -- this table
-- holds the full audit trail, including NO_MATCH and UNCERTAIN pairs.
CREATE TABLE match_candidates (
    candidate_id VARCHAR PRIMARY KEY,

    listing_id_a VARCHAR,
    listing_id_b VARCHAR,

    blocking_method VARCHAR,
    match_method VARCHAR,
    match_confidence DOUBLE,

    decision VARCHAR,

    review_status VARCHAR,

    created_at TIMESTAMP
);

-- Market Engine (Milestone 7): deterministic, DuckDB/Python-only metrics.
-- Field names deliberately say "observed", not "market size" -- these are
-- SellerSprite-derived observations for the relevant, resolved listings we
-- have, not a claim of total Amazon market coverage. See
-- docs/market_metrics.md for every formula.
CREATE TABLE product_market_metrics (
    product_id VARCHAR PRIMARY KEY,
    category_id VARCHAR,

    total_listing_count INTEGER,
    listings_with_price_data INTEGER,
    listings_with_sales_data INTEGER,

    best_listing_id VARCHAR,
    best_listing_observed_monthly_sales DOUBLE,
    best_listing_annualized_observed_sales DOUBLE,

    observed_monthly_revenue DOUBLE,
    annualized_observed_revenue DOUBLE,

    min_price DOUBLE,
    max_price DOUBLE,
    median_price DOUBLE,
    representative_price DOUBLE,

    rating DOUBLE,
    review_count INTEGER,

    calculated_at TIMESTAMP
);

CREATE TABLE category_market_metrics (
    category_id VARCHAR PRIMARY KEY,

    total_product_count INTEGER,
    total_listing_count INTEGER,

    total_observed_monthly_sales DOUBLE,
    annualized_observed_sales DOUBLE,
    total_observed_monthly_revenue DOUBLE,
    annualized_observed_revenue DOUBLE,

    price_distribution VARCHAR,          -- JSON: see docs/market_metrics.md
    sales_distribution VARCHAR,          -- JSON
    listing_concentration_hhi DOUBLE,
    product_type_distribution VARCHAR,   -- JSON

    calculated_at TIMESTAMP
);

-- Opportunity Engine (Milestone 9): evidence-based signals, never an
-- opaque AI verdict ("AI says this is a winning product"). Every signal's
-- signal_strength/confidence must be traceable back to the specific
-- numbers/quotes in evidence/supporting_metrics/supporting_review_themes.
-- See docs/opportunity_signals.md.
--
-- signal_type is a closed taxonomy: PRODUCT_IMPROVEMENT, BUNDLE,
-- PRICE_SEGMENT, UNDERREPRESENTED_PRODUCT_TYPE, CUSTOMER_PAIN_POINT,
-- COMPETITIVE_CONCENTRATION (src/dmie/opportunity/signals.py::SIGNAL_TYPES).
-- product_id is NULL for category-level signal types (PRICE_SEGMENT,
-- UNDERREPRESENTED_PRODUCT_TYPE, COMPETITIVE_CONCENTRATION) -- they
-- describe a gap or structural property of the category, not an existing
-- product.
CREATE TABLE opportunity_signals (
    signal_id VARCHAR PRIMARY KEY,

    product_id VARCHAR,
    category_id VARCHAR,

    signal_type VARCHAR,
    status VARCHAR,           -- signal_present | insufficient_data | signal_absent
    signal_strength VARCHAR,  -- LOW | MEDIUM | HIGH | NULL (only meaningful when status = signal_present)
    confidence DOUBLE,        -- data-coverage confidence, distinct from signal_strength

    evidence VARCHAR,                   -- JSON: narrative summary + conditions + metrics snapshot
    supporting_metrics VARCHAR,         -- JSON: list of market-engine-derived conditions
    supporting_review_themes VARCHAR,   -- JSON: {conditions, themes: [{theme, frequency, mean_severity, sample_evidence}]}

    created_at TIMESTAMP
);

CREATE TABLE review_insights (
    insight_id VARCHAR PRIMARY KEY,

    product_id VARCHAR,
    listing_id VARCHAR,

    pain_point VARCHAR,
    attribute VARCHAR,

    severity INTEGER,
    evidence_text VARCHAR,

    improvement_opportunity VARCHAR,

    confidence DOUBLE,

    model_version VARCHAR,
    prompt_version VARCHAR,

    created_at TIMESTAMP
);

CREATE TABLE decision_log (
    decision_id VARCHAR PRIMARY KEY,

    entity_type VARCHAR,
    entity_id VARCHAR,

    decision_type VARCHAR,

    old_value VARCHAR,
    new_value VARCHAR,

    reason VARCHAR,

    actor VARCHAR,

    created_at TIMESTAMP
);

-- Run versioning and data provenance (Milestone 14). Append-only: unlike
-- every _runs table's DELETE-and-regenerate derived DATA tables above
-- (match_candidates, product_listings, products, listing_classification,
-- listing_product_type_classification, ...), rows here are NEVER deleted
-- or overwritten by a pipeline re-run -- every execution gets its own new
-- row, so "what did the last 3 runs produce" is always answerable. See
-- docs/pipeline_runs.md and DECISIONS.md "M14 -- Run Versioning".
--
-- The DATA tables themselves still reflect only the CURRENT run's output
-- (regenerating them in place is unchanged by this milestone -- a much
-- larger, separate architectural change -- see DECISIONS.md for why that
-- boundary was deliberately not crossed here). This gives real,
-- queryable run history without touching how any existing table is
-- written to.
CREATE TABLE pipeline_runs (
    run_id VARCHAR PRIMARY KEY,

    category_id VARCHAR,
    pipeline_stage VARCHAR,    -- e.g. 'relevance_classification', 'product_type_classification', 'product_resolution'
    pipeline_version VARCHAR,  -- that stage's own version constant (CLASSIFIER_VERSION, MATCHING_VERSION, ...)

    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    status VARCHAR,            -- running | success | failed
    error_message VARCHAR,

    summary VARCHAR,            -- JSON: the same counts the script prints to stdout, persisted

    created_at TIMESTAMP
);

-- One row per classify.py or classify_product_types.py run (`stage`
-- distinguishes them) -- the specific, comparable counts a human would
-- want when comparing two runs, rather than parsing the generic
-- pipeline_runs.summary JSON blob.
CREATE TABLE classification_runs (
    run_id VARCHAR PRIMARY KEY,  -- references pipeline_runs.run_id

    category_id VARCHAR,
    stage VARCHAR,               -- 'relevance' | 'product_type'
    classifier_version VARCHAR,

    total_classified INTEGER,
    class_counts VARCHAR,         -- JSON: {class_or_type: count}
    auto_accepted_count INTEGER,
    needs_review_count INTEGER,

    created_at TIMESTAMP
);

CREATE TABLE product_resolution_runs (
    run_id VARCHAR PRIMARY KEY,  -- references pipeline_runs.run_id

    category_id VARCHAR,
    matching_version VARCHAR,

    total_listings INTEGER,
    candidate_pairs INTEGER,
    match_count INTEGER,
    no_match_count INTEGER,
    uncertain_count INTEGER,
    resulting_products INTEGER,

    created_at TIMESTAMP
);

-- Data Operations Center (Milestone 16). Tracks an uploaded raw dataset
-- through Upload -> Temporary storage -> Validation -> Approval ->
-- Pipeline execution. Never the production `listings` table directly --
-- an upload only reaches `listings` via the existing ingest.py path,
-- triggered from `approved`, exactly like a manually-placed file would.
-- See docs/data_platform_architecture.md.
CREATE TABLE dataset_uploads (
    upload_id VARCHAR PRIMARY KEY,

    category_id VARCHAR,
    dataset_type VARCHAR,      -- full_export | incremental_update
    version VARCHAR,

    original_filename VARCHAR,
    staging_path VARCHAR,      -- data/staging/... -- never data/raw/ until approved
    raw_path VARCHAR,          -- data/raw/... once approved (immutable from then on)

    row_count INTEGER,

    status VARCHAR,            -- staged | validated | approved | rejected | ingested
    validation_status VARCHAR, -- PASS | WARNING | FAIL
    validation_report VARCHAR, -- JSON: list of {check, severity, message, details}
    quality_score DOUBLE,      -- 0-100, see dataset_validator.py::ValidationReport.quality_score (Tier 1 addition)

    pipeline_run_id VARCHAR,   -- references pipeline_runs.run_id, set once ingested

    uploaded_at TIMESTAMP,
    validated_at TIMESTAMP,
    approved_at TIMESTAMP,
    ingested_at TIMESTAMP
);

-- Dataset version registry (Milestone 16): one row per dataset that
-- actually reached `listings` (i.e. an upload that completed the full
-- Upload -> ... -> Pipeline execution flow). Deliberately separate from
-- dataset_uploads, the same way pipeline_runs is separate from
-- classification_runs/product_resolution_runs -- this table answers
-- "what versions of this category's data have we ever ingested", not
-- "what is this one upload's lifecycle status".
CREATE TABLE dataset_versions (
    version_id VARCHAR PRIMARY KEY,

    category_id VARCHAR,
    source VARCHAR,        -- connector name, e.g. 'sellersprite'
    version VARCHAR,
    upload_id VARCHAR,      -- references dataset_uploads.upload_id

    record_count INTEGER,

    created_at TIMESTAMP
);

-- Snapshot System (Milestone 16): a point-in-time capture of
-- product_market_metrics for a category, taken before and after every
-- pipeline run, so market_changes below has something concrete to diff.
-- snapshot_data is a JSON object {product_id: {price, monthly_sales,
-- observed_monthly_revenue, total_listing_count}} -- a deliberately
-- narrow snapshot of the fields change detection actually needs, not a
-- full row dump. See docs/data_platform_architecture.md.
CREATE TABLE market_snapshots (
    snapshot_id VARCHAR PRIMARY KEY,

    category_id VARCHAR,
    pipeline_run_id VARCHAR,   -- references pipeline_runs.run_id -- NULL for an ad-hoc snapshot
    taken_at TIMESTAMP,

    product_count INTEGER,
    listing_count INTEGER,

    snapshot_data VARCHAR      -- JSON
);

-- Change Detection Foundation (Milestone 16): one row per detected
-- change between two consecutive market_snapshots for a category.
-- change_type is a closed taxonomy: NEW_PRODUCT, REMOVED_PRODUCT,
-- NEW_LISTING, PRICE_CHANGE, SALES_CHANGE. entity_id is a product_id for
-- every type here (listing-level counts are tracked via
-- total_listing_count on the NEW_LISTING type, not a separate
-- listing-level row, since market_snapshots itself is product-level).
CREATE TABLE market_changes (
    change_id VARCHAR PRIMARY KEY,

    category_id VARCHAR,
    snapshot_id_before VARCHAR,
    snapshot_id_after VARCHAR,

    change_type VARCHAR,
    entity_id VARCHAR,          -- product_id

    old_value VARCHAR,
    new_value VARCHAR,

    detected_at TIMESTAMP
);

-- Personnel Intelligence (Milestone 12) -- real data, not a schema-only
-- placeholder. Sourced from a real `产品经理` (Product Manager) column
-- found in a broader merged multi-category SellerSprite export
-- (scripts/extract_from_merged_export.py writes
-- data/raw/employee_category_ownership.csv), previously listed as an
-- "unmapped" raw field in docs/data_dictionary.md since the pilot's
-- very first milestone. employee_id is a deterministic slug of the
-- real Chinese name (see dmie.database.personnel), not an invented id.
CREATE TABLE employees (
    employee_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    created_at TIMESTAMP
);

-- One row per (employee, raw category label) pair actually observed in
-- a source export -- raw_category_label is the real Chinese 二级类目
-- string, kept even when it doesn't correspond to any category this
-- project has onboarded (matched_category_id is NULL in that case).
-- Only ~154 raw categories exist in the source data -- most have no
-- taxonomy/pipeline here yet -- this table records real ownership
-- breadth honestly without pretending every one of those categories has
-- full intelligence behind it. See docs/personnel_intelligence.md.
CREATE TABLE employee_category_ownership (
    ownership_id VARCHAR PRIMARY KEY,

    employee_id VARCHAR,
    raw_category_label VARCHAR NOT NULL,
    matched_category_id VARCHAR,  -- references categories.category_id -- NULL if not onboarded

    listing_count INTEGER,        -- real row count from the source export, this employee/category pair
    source_file VARCHAR,

    created_at TIMESTAMP
);
