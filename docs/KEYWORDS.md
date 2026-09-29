# Keyword demand

`src/dip/keywords.py`; settings in `config/platform/keywords.yaml`; page `/keywords` (Discover).

## What it solves

Listing exports show what sells. Keyword exports show what shoppers look for, and how crowded each search is.

Importing a keyword export (for example SellerSprite 关键词挖掘 / keyword research, or 反查关键词 / reverse ASIN) gives four things:

- **Keyword demand:** monthly searches, purchases and purchase rate.
- **Competition:** products, title density, PPC bid and click concentration.
- **Sub-category:** each keyword is placed in a sub-category of the market.
- **Under-served keywords:** real demand that few first-page listings name in their title. These are a listing or launch angle.

## How it works (deterministic, no model)

1. **Headers.** Columns are mapped with the synonyms in `columns:`, in English or Chinese. Matching works like listing imports. A title line above the header row is skipped.
   - The import returns the mapping and the unmapped columns.
   - Percent text (`6.0%`) and plain rates (`12.5` in a rate column) both become fractions.
2. **Nothing is dropped silently.** Blank rows are counted. Duplicate keywords keep the row with the most searches. Both appear in the import notes.
3. **Sub-category.** A listing matches a keyword when its title contains every content word of the keyword (stop words and plural `s` are ignored).
   - The keyword goes to the sub-category holding most of the matched listings' sales. That share is shown as confidence.
   - Keywords that match fewer than `matching.min_listings` listings stay **unassigned**. They are kept and listed.
4. **Score (0–100):** demand against competition. Each component is a percentile rank inside the imported list:
   - more searches and a higher purchase rate score higher;
   - fewer products, lower title density, a lower bid and less concentrated clicks score higher.
   - Weights are in `score.weights`. A column the export lacks is left out, and `coverage` says how much of the weight was available.
5. **Under-served:** at least the list's median searches, and at most `gap.max_title_density` first-page listings naming the keyword in their title.

## Using it

- **Upload:** `POST /markets/{market}/keywords` with a CSV or Excel file. It replaces that market's previous list.
- **Read:**
  - `GET /markets/{market}/keywords`, with optional filters `segment` (or `segment=unassigned`), `gaps`, `q`, `sort`, `desc`, `limit` and `offset`;
  - `GET /markets/{market}/keywords/summary` for demand by sub-category.
- **New headers:** a tool that names columns differently needs a synonym in `config/platform/keywords.yaml`, not code.
