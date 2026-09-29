---
version: review_analysis_v1
used_by: src/dmie/reviews/extraction.py:extract_with_ai
---

You are analyzing customer reviews for an Amazon dental product.

Extract only claims supported by the supplied review.

Identify:

1. pain point
2. affected product attribute
3. severity
4. customer complaint
5. evidence
6. potential product improvement

Do not invent causes.

Do not infer facts not contained in the review.

## Controlled taxonomy

The pain point MUST be one `category/subcategory` pair from this exact
list — never invent a new category or subcategory:

{TAXONOMY}

## Severity scale (1-5)

1. Minor/cosmetic annoyance — does not affect core function
2. Mild inconvenience — workaround exists, product still usable
3. Moderate functional problem — noticeably impairs the product's job
4. Major functional failure — product mostly fails at its job
5. Product unusable, or a safety concern

## Evidence rule

`evidence` must be an exact quoted substring copied from the review
below — not a paraphrase, not a summary, not an inference. If you cannot
find a substring that supports the pain point, do not report that pain
point at all.

<review>
{REVIEW}
</review>

## Output

Respond with ONLY a JSON object, no other text:

```json
{
  "pain_point_category": "one of the 6 top-level taxonomy categories",
  "pain_point_subcategory": "one of that category's subcategories",
  "affected_attribute": "short free-text product attribute this affects",
  "severity": 1,
  "customer_complaint": "short paraphrase of the complaint, grounded in the review",
  "evidence": "exact substring copied from the review",
  "potential_improvement": "short suggestion tied directly to the complaint",
  "confidence": 0.0
}
```

If the review contains no genuine complaint (e.g. it's purely positive,
or too vague to support any taxonomy category with real evidence), return
`{"no_pain_point_found": true}` instead of forcing a category.
