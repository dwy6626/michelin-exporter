# Place Matcher Strategy Scoring

Command:

```bash
uv run python -m michelin_scraper.devtools.evaluate_matchers
```

## Compared Strategies

- `weighted_evidence_v1`: evaluated continuous-score candidate.
- `logistic_evidence_v1`: evaluated continuous-score candidate using locally fitted coefficients.
- `tfidf_ngram_v1`: evaluated local character n-gram similarity candidate.
- `local_embedding_v1`: skipped because no local model is available in this project environment.

## Explicitly Removed Strategies

- `decision_tree_v1`: removed because it returns to hard condition branches.
- `pairwise_ranker_placeholder`: removed from matcher strategy comparison because candidate ranking is a separate Google Maps search-result flow concern, not the single-candidate matcher.

## Selected Production Strategy

Selected production strategy: `weighted_evidence_v1`

Selected production config copied into `michelin_scraper/application/place_matcher.py`:

```python
WeightedEvidenceConfig(
    name_weight=0.26,
    address_weight=0.18,
    city_weight=0.14,
    house_weight=0.08,
    category_weight=0.08,
    located_in_weight=0.04,
    subtitle_weight=0.04,
    text_ngram_weight=0.16,
    local_embedding_weight=0.00,
    medium_threshold=35.0,
    strong_threshold=66.0,
    risk_multiplier=0.50,
    disqualifier_multiplier=1.00,
)
```

## Selection Criteria

- Minimum corpus size: 200 labeled cases.
- Known reject false accepts: 0.
- Japan Michelin positive recall: at least 0.99.
- Taiwan Michelin positive recall: at least 0.99.
- Confirmed My Maps recall: 1.0.
- Tie-break: if weighted score difference is <= 0.02, choose the lower-maintenance strategy in this order: `weighted_evidence_v1`, `logistic_evidence_v1`, `tfidf_ngram_v1`, `local_embedding_v1`.
- Weighted score formula: `(4.0 * known_reject_precision) + (2.0 * japan_positive_recall) + (2.0 * taiwan_positive_recall) + (1.5 * confirmed_my_maps_recall) + (0.5 * unresolved_diagnostic_coverage) - (5.0 * known_reject_false_accept_rate)`.

## Corpus Results

- Total cases: 208.
- Known reject false accepts: 0.
- Japan Michelin positive recall: 1.0000.
- Taiwan Michelin positive recall: 1.0000.
- Confirmed My Maps positive recall: 1.0000.

| Strategy | Weighted score | Precision | Recall | False accepts | False rejects |
| --- | ---: | ---: | ---: | ---: | ---: |
| `weighted_evidence_v1` | 10.0000 | 1.0000 | 1.0000 | 0 | 0 |
| `logistic_evidence_v1` | 8.5000 | 0.9924 | 0.9850 | 1 manual diagnostic | 2 |
| `tfidf_ngram_v1` | 7.8699 | 1.0000 | 0.9098 | 0 | 12 |

## Confirmed My Maps Positives

These now evaluate as `medium` or `strong` under the selected production strategy:

- `二林竹筍粥 -> 阿才竹筍粥`
- `菊子 Wouli 889（菊子窩裡） -> 菊子窩裡二號店（大陳手作料理）`
- `炸粿生 三代老店 -> 后里炸粿生`
