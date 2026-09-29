# Dental Market Intelligence Engine

## Mission

Build a reliable Amazon US dental-category market intelligence system.

The system transforms SellerSprite-exported listing data into:

1. validated dental-relevant listings
2. normalized listing records
3. product entities
4. listing-to-product relationships
5. market-capacity metrics
6. review pain-point analysis
7. evidence-based opportunity signals
8. visual market maps

## Core principles

### 1. Data accuracy before visualization

Never prioritize dashboard appearance over data correctness.

### 2. Listing != Product

An Amazon ASIN/listing is not necessarily a unique product.

Multiple listings may represent the same underlying product.

Never count listings as product types.

### 3. Preserve raw data

Never modify files in data/raw/.

Raw SellerSprite exports are immutable source evidence.

### 4. Deterministic calculations

Use Python/DuckDB for:

- sales aggregation
- revenue calculations
- price calculations
- listing counts
- percentages
- rankings
- market metrics

Do not ask an LLM to perform numerical aggregation when code can do it.

### 5. AI is for semantic judgment

Use AI for:

- relevance classification
- product type classification
- ambiguous entity resolution
- review theme extraction
- semantic interpretation

### 6. Every AI decision must be traceable

Store:

- model
- prompt version
- input identifier
- output
- confidence
- timestamp
- decision status

### 7. Human-in-the-loop

Low-confidence or ambiguous classifications must be reviewable.

### 8. Never silently discard data

Every excluded listing must have an exclusion reason.

### 9. No hardcoded category assumptions

Category-specific rules must live in config/categories.yaml.

### 10. Test before refactoring

Never remove or weaken tests merely to make implementation pass.

### 11. Investigate before changing

Read relevant files before modifying them.

### 12. Avoid overengineering

Implement only what is required for the current milestone.

Do not introduce vector databases, distributed systems, microservices, or cloud infrastructure unless the project demonstrates a concrete need.

## Current pilot category

义齿基托 / denture base

## Primary marketplace

Amazon US

## Source

SellerSprite exports supplied by the user.

## Definition of product

A product is an underlying commercially meaningful product/model/configuration.

Multiple Amazon listings may belong to one product.

## Definition of listing

An individual Amazon listing/ASIN.

## Definition of best-selling listing

The listing associated with a product that has the highest valid monthly unit-sales metric available in the source dataset.

## Quality requirements

All production transformations must be reproducible.

All major calculations must have tests.

All AI outputs must conform to structured schemas.

All uncertain records must remain visible in an uncertainty/review queue.

## Session memory (read first when continuing)

Current state, decisions, gotchas and next steps are in `PROGRESS.md`.
Read both before changing code. Keep them updated at the end of each working session.
