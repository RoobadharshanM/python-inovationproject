# Training scripts

Run these only after importing the Kaggle data and applying migrations:

```bash
python scripts/train_score_model.py
python scripts/train_outcome_model.py
```

The scripts read normalized Django database data, create a chronological split,
train scikit-learn pipelines, print measured evaluation metrics, and write
artifacts to `data/models/`. Normal web requests only load these artifacts and
never retrain them.
