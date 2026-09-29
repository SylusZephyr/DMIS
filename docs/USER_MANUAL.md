# User Manual

## 1. Start the platform

**Windows:** double-click `run_platform.bat`. **macOS/Linux:** `bash run_platform.sh`.
Either one installs the dependencies, builds the data from `data/raw/` on the first run, starts
the API on port 8000 and opens the command center at http://localhost:3000.

**Manual:**

```bash
pip install -e ".[platform]"            # add ,ml for the XGBoost relevance model
python scripts/dmis.py bootstrap         # ownership file + every data/raw/<market>/ export
python scripts/dmis.py serve             # API: http://localhost:8000/docs
cd frontend && npm install && npm run dev
```

**Signing in:**

- On a laptop the platform is single-user and sign-in is off.
- With PostgreSQL, or with `DIP_AUTH=on`, every user needs a token. Create one with:

  ```bash
  python scripts/dmis.py create-user you@company.com "Your Name" --role admin
  python scripts/dmis.py create-user pm@company.com "PM Name" --role product_manager --employee "PM Name"
  python scripts/dmis.py create-user buyer@clinic.com "Buyer" --role customer
  ```

  Paste the printed token into the sign-in dialog. A product manager sees only the markets of
  the categories assigned to their employee. A customer sees Shopping Mode only.

## 2. Load data

**Data Operations → Upload:**

- Choose the export (SellerSprite XLSX, or any CSV/XLSX/JSON with product rows).
- Name the market.
- Optionally give the snapshot date and a reviews file (columns id/asin, text, rating).

The job runs 18 stages with live progress. The ingestion report shows:

- accepted and rejected rows, with the reason for each rejection
- the column mapping
- any warnings

**Command-line equivalents:**

- one file: `python scripts/dmis.py process FILE --market NAME [--snapshot-date YYYY-MM-DD] [--reviews FILE]`
- a folder of monthly exports: `python scripts/dmis.py process-dir FOLDER --market NAME`

**Keep uploading new exports.** Each new snapshot of a market adds history:

- trends get a growth number and higher confidence
- confidence gains historical consistency
- competitor changes and alerts appear

Uploading an identical file is skipped instantly; tick *force* to re-run it.

**Other inputs:**

- **Suppliers:** Suppliers page → import CSV/XLSX, or add one with the form.
- **Correcting relevance:** on the review queue in Data Operations, mark a listing dental or not. The correction applies on the next run and trains the local model.
- **Live sources:** set the environment variables shown on **Alerts & Events → Live sources**, then run `python scripts/dmis.py poll`.

## 3. Language

The switch at the bottom of the sidebar, **EN / 中文**, changes every page. The choice is remembered in this browser. Product titles and brands are data, so they stay as they are in the source.

## 4. Pages

### Intelligence

| Page | What it answers |
|---|---|
| **Command Center** | **Where to act.** Each market shows its estimated monthly revenue with the 95 % interval, its evidence grade, its best segment by opportunity index, and the product the evidence recommends. The page also shows leading brands by estimated revenue and share, and a world view of markets and suppliers. |
| **Market Universe** | The industry → branches → markets → segments. Size shows estimated revenue, colour the opportunity index, and glow the revenue share from new listings. |
| **Product Galaxy** | Every product as a star placed by title similarity. Colour by segment, sub-category, brand, price quartile, opportunity, margin or entrant status; the legend names each colour, and clicking an entry isolates it. Size is by estimated revenue or units. You can switch on three link types: similar, same brand and same segment. Search, filter, select a product to see its links, and compare up to 4 products. |
| **Market Analytics** | One market in depth. It shows size (estimate, interval and certain floor), segments ranked by opportunity index with a generated description and keywords, the price × demand matrix with a time control, entry and economics, the quality gap, demand gaps, the recommended product, and the category boundary, where you can confirm or override which sub-categories belong. Click **ⓘ** on any number to see how it is computed, its interval, n, basis and caveat, with a link to the formula. |
| **Market Analytics (continued)** | **Growth & forecast** fits a trend through the revenue estimates of every monthly snapshot. It shows growth per month with its interval, whether the trend is significant, and 3/6/12-month forecasts with prediction intervals. A ⚠ marks a forecast that reaches beyond the history. Expanding a segment shows its top brands (share interval, P(#1)), demand per listing by price band, and launch momentum. On a product page, **Position in its sub-category** shows where the product ranks on price, units, revenue and rating, with a scatter plot of its peers and a button that opens the Launch Simulator pre-filled. |
| **Knowledge Graph** | A typed ontology: market → sub-category → segment → product → listing, plus brands, demand gaps, recommendations, momentum and suppliers. Every edge carries its evidence: click an edge to see why it exists. Select two nodes to see how they connect, hop by hop, with the strongest evidence preferred. You can filter by edge type and minimum strength. |

### Decisions

| Page | What it answers |
|---|---|
| **Opportunity Board** | **What to sell.** For each market's top sub-categories you get the product concept the evidence supports: its significant features and price, or the typical product at the median price when the sub-category is too small to test. Each concept sits next to its simulated launch (units, revenue, chance of reaching the revenue target, risks), the sub-category's growth, the leading brand's share with P(#1), and entrant success. Sort by any column; expand a row for the reasoning and a one-click launch simulation. Nothing is re-scored: these are the same numbers as on the other pages. |
| **Market Analyst** | Ask in English or Chinese, for example "What should we sell in dental models?", "谁在领导牙科模型市场？" or "What happens if we launch at $19.99 with cost $5?". The answer is a list of computed facts. Each fact has a *verify* link to the page showing the same number. The optional AI phrasing (Google Gemini, `GEMINI_API_KEY`) may only restate the facts: a reply containing any number that is not in the facts is rejected. |
| **Launch Simulator** | Describe a product, its price and your unit cost. You get: expected units, revenue and profit as distributions; the chance of making money and of reaching a target; break-even and payback; the expected profit at every price in the segment's range; which statistically significant demand gaps the idea covers; risks, each with its number; what the segment's actual new listings sell; and comparable listings. **Compare scenarios** runs several prices or costs on the same random numbers. Without your unit cost no profit is shown; the source's "cost" column is derived from price and is never used as a cost. |
| **Competitors & Trends** | Brand shares of estimated revenue with 95 % intervals, rank ranges and P(#1). A brand is "above par" or "below par" only when its whole interval is above or below the equal-share benchmark. Price index, Bayesian rating, entrants, and strengths or weaknesses with the opening each creates. Launch cohorts and launch momentum (exact binomial test). Share changes between two snapshots are shown only as significant or within noise. |
| **Alerts & Events** | A change reaches the category owner only when it is statistically significant against the intervals of both snapshots. Every event shows its test. Changes that were not significant stay in the log as *info*. |

### Workflow

| Page | What it answers |
|---|---|
| **Product Pipeline** | Ideas → evaluation → supplier search → prototype → launch decision (manager approval) → launched. A project's prediction is a launch simulation on the market's demand model, and inputs are validated. After launch, add the listing IDs: actual sales are compared with the predicted p10–p90 range. Badge data is compared as an interval. |
| **Inbox, Audit Log, Organization** | Daily summary and messages; every change and who made it; plan, usage and API tokens. |

### Quality

| Page | What it answers |
|---|---|
| **Accuracy** | How far the numbers can be trusted. **Run validation** tests the production demand model on markets with a known truth: size error, interval coverage and whether it finds the top sub-category. Each real market's hold-out check shows pass, watch or fail. The page also shows human-label precision and recall, and launch predictions against reality. |
| **Accuracy labelling** | Judge a random stratified sample of the platform's conclusions. These labels are the only source of precision and recall numbers. |
| **Feedback & usage** | Every "This looks wrong" report, and which pages are used. |

### Operations and modes

| Page | What it does |
|---|---|
| **Data Operations** | Upload exports and follow each processing stage. Excluded records are listed with their reason, and you can correct relevance decisions there. |
| **Methodology** | Every formula, with what it was validated against. |
| **Suppliers** | Your supplier list, cooperation history and ranking. |
| **My Portfolio** | For each product manager: the categories they own, the recommended actions (with the numbers behind them), and a market status per market. |
| **Shopping Mode** | For buyers. Products are compared on requirement match, Bayesian rating, proven demand (the lower bound of estimated sales) and price. The Pareto front marks products that no other product beats on every criterion. A dominated product names the product that beats it. You can change the weights. |

## 5. Reading the numbers

- **Estimate and 95 % interval.** Most sources show sales badges ("200+ bought"). A badge is a range, and a listing without a badge sells below the first rung. The demand model places every listing inside what the source shows and estimates the rest. The interval is where the true value lies 95 % of the time. On synthetic markets it contains the truth ≥ 90 % of the time, which the Accuracy page measures.
- **Floor.** This is the certain lower bound: every listing at the bottom of its badge. Tools that add up badges report this number. It is typically about a third below the truth.
- **Evidence grade A–D.** This reflects how much the data can say: the number of listings, the share with a badge, and the hold-out AUC. At grade C or D, read the intervals, not the point estimates.
- **Opportunity index (0–100).** A weighted geometric mean of six components: demand, entry, margin, competition, quality gap and saturation. Each component is on an absolute scale, so indices can be compared across markets. A missing component is left out, and the coverage is shown.
- **Totals add up.** A market's estimate is exactly the sum of its segments, products, brands and listings. Each figure is a sum of the listings' expected sales, and its interval comes from the same simulation.
- **Growth.** A trend is claimed only after at least 3 dated snapshots and a significant test. Otherwise the page says how many snapshots exist.
- **Significant.** A difference is called significant only after a test, with false-discovery control across everything tested at once. "Within noise" means the data cannot tell the difference from zero.

## 6. Tips

- Upload a new export every month with its snapshot date. Share changes, significance-tested alerts and growth all need more than one snapshot.
- Enter your real unit cost in the Launch Simulator and on projects. The platform will not invent one.
- If a number looks wrong, use **This looks wrong** on that page. Every report is reviewed.
