# Designing ML Systems — Master Notes
### Theory (every point, Ch. 1–7) + Production Code Patterns + Modern Additions

> Source: *Designing Machine Learning Systems* (Chip Huyen, early release) — Ch.1 ML Systems in Production, Ch.2 Data Engineering Fundamentals, Ch.3 Training Data, Ch.4 Feature Engineering, Ch.5 Model Development, Ch.6 Deployment, Ch.7 Why ML Systems Fail.
> Tags: **[BOOK]** = from the text · **[+]** = added modern/practical material · **[CODE]** = production pattern.

---

## 0. The One-Page Mental Model

```
Project scoping → Data engineering → Training data → Features → Model dev/eval
      ↑                                                              ↓
Business analysis ← Monitoring & continual learning ← Deployment ←───┘
```

- ML system = **interface + data stack + ML algorithm + infrastructure + hardware**. Algorithm is the *small* part.
- ML is **iterative, never-ending**: a cycle, not a line.
- Production ≠ research: stakeholders, latency, shifting data, fairness, interpretability all change.
- ML systems are **part code, part data, part artifacts** → version/test all three.
- ML systems **fail silently**. Monitoring is a first-class feature.
- Most ML failures in production are **engineering/data failures**, not model failures (Google study: 60/96 pipeline outages not ML-related).

---

# PART 1 — Chapter 1: ML Systems in Production

## 1.1 When to use ML **[BOOK]**

**Definition:** ML learns **complex patterns** from **existing data** and uses them to make **predictions** on **unseen data**.

| Keyword | Meaning | Implication |
|---|---|---|
| **Learn** | System has capacity to learn (a relational DB doesn't) | Needs something to learn from (data) |
| **Complex** | Patterns too complex to hand-code | Zip→state = lookup table, no ML. Price from listing features = ML |
| **Patterns** | There must be patterns | Fair die: no pattern. Stocks: patterns (maybe). Absence of success ≠ absence of pattern |
| **Existing data** | Data available or collectable | Zero-shot: pretrained on related data. Online learning: learns in prod (risk: poor UX). "Fake-it-till-you-make-it": humans generate predictions → data |
| **Predictions** | Predictive problem | Anything can be reframed: "what would the answer be?" — esp. compute-heavy problems (denoising, shading approximated by ML) |
| **Unseen data** | Same distribution as training | Koi Pond app example — train 2008, predict Christmas 2020 fails |

**Bonus characteristics where ML shines:**
1. **Repetitive** (patterns repeat → easier to learn; most ML needs many examples, humans do few-shot)
2. **Cost of wrong prediction is cheap** (recommenders: user just doesn't click). If catastrophic, need benefit > cost (self-driving: statistically safer than humans)
3. **At scale** (upfront investment in data/compute/infra/talent). One prediction may hide a *series* (election forecast updated hourly)
4. **Patterns constantly changing** (spam; hand-written rules go stale, ML retrains on new data)

**Don't use ML when:** (1) unethical, (2) simpler solution works, (3) not cost-effective.
- Decompose: can't build full chatbot → ML classifies "is this an FAQ?" → route.
- Don't dismiss tech because it's not cost-effective *now* (technology advances are incremental).

## 1.2 Use cases **[BOOK]**

- **Consumer:** search, recommendations, predictive typing, photo enhancement, face/fingerprint auth, machine translation, smart assistants, cameras, fall detection.
- **Enterprise (majority of ML use):** stricter accuracy needs (0.1% efficiency = millions saved) but more latency tolerance. Consumer apps: easier to distribute, harder to monetize.
- Enterprise examples: **fraud detection** (oldest; anomaly detection), **price optimization** (maximize objective like margin; dynamic markets: ads, flights, ride-share), **demand forecasting** (inventory), **customer acquisition cost** reduction (targeting, discount timing), **churn prediction** (acquiring costs 5–25× retaining; also employees), **support ticket classification**, **brand monitoring** (sentiment; explicit + implicit mentions), **healthcare** (skin cancer, diabetes — delivered via providers due to accuracy/privacy).

## 1.3 Mind vs. Data **[BOOK]**

- Mind camp: Judea Pearl ("Data is profoundly dumb"), Chris Manning (structure lets systems learn from less).
- Data camp: Sutton's *Bitter Lesson* (general methods leveraging computation win), Norvig ("we don't have better algorithms, just more data").
- Rogati's **Data Science Hierarchy of Needs**: collect → move/store → explore/transform → aggregate/label → learn/optimize (AI at the top).
- Debate isn't "is data necessary" but "is it sufficient". Finite ≠ infinite data (infinite = lookup).
- Dataset growth: 1B Words (0.8B tokens, 2013) → GPT-2 (10B) → GPT-3 (500B).
- **More data ≠ better** if outdated or mislabeled.

## 1.4 Research vs. Production **[BOOK]**

| | Research | Production |
|---|---|---|
| Requirements | SOTA on benchmarks | Multiple stakeholders, conflicting |
| Compute priority | Fast **training**, high **throughput** | Fast **inference**, low **latency** |
| Data | Static, clean, historical | Messy, shifting, streaming + historical, privacy/regulatory |
| Fairness | Good to have | Important |
| Interpretability | Good to have | Important |

### Stakeholders (restaurant-rec example)
ML engineers → most-clicked model; Sales → high-fee (expensive) restaurants; Product → <100ms latency; ML platform → hold off updates; Manager → maximize margin (cut ML team!).
- **Decouple objectives** (Ch.5): one model per objective, combine scores.
- Know which requirements are **must-have vs nice-to-have**.
- Ensembling wins Kaggle/Netflix Prize but rarely used in prod (complexity, latency, interpretability).
- Small gains matter when revenue-sensitive (0.2% CTR = millions) but not when users can't perceive (95% vs 95.2% ASR). Complex models must clearly beat simple ones to justify cost.
- Leaderboard critiques: hard steps already done for you; multiple-hypothesis testing on same holdout → winners by chance; Ethayarajh & Jurafsky: benchmarks reward accuracy at expense of compactness/fairness/energy.

### Latency vs Throughput
- **Latency** = time from request to response (book uses "latency" = response time; Kleppmann distinguishes service time/queueing).
- **Throughput** = queries per time.
- Serial: latency 10ms → 100 qps; 100ms → 10 qps.
- Batching: 10 queries/batch @10ms = 1000 qps; 100/batch @50ms = 2000 qps → **higher latency AND higher throughput**. Online batching adds wait time.
- Evidence latency matters: Akamai 100ms → −7% conversion; Booking.com +30% latency → −0.5% conversion; Google: >50% mobile users leave if page >3s.
- **Latency is a distribution.** Example: [100,102,100,100,99,104,110,90,3000,95] → mean 390ms misleading. Use **percentiles**: p50 (median), p90, p95, p99, p99.9. Highest-latency users are often the most valuable (most data, Amazon).
  - *Note [+]*: with 10 samples p90 depends on interpolation method (nearest-rank gives 110 or 3000). Use ≥1000s samples in reality.
- SLOs specified in percentiles: "p99 < 200ms".

### Data in production
- Noisy, unstructured, biased (unknown how), labels sparse/imbalanced/outdated/wrong; label classes may change after deployment; privacy; plus streaming + third-party.

### Fairness
- Biases: zip code → loan denial; name spelling → resume rank; credit scores → mortgage rates. Berkeley study: 1.3M creditworthy Black/Latino applicants rejected 2008–2015.
- **ML doesn't predict the future, it encodes the past** and does so at scale.
- Minorities suffer since misclassification barely dents aggregate metrics. Only 13% of large firms mitigating fairness risk (McKinsey 2019).

### Interpretability
- Hinton's AI-surgeon question: 90% black-box vs 80% human — executives split 50/50.
- Needed for users (trust, bias detection) and developers (debugging). Only 19% of large companies working on explainability (2019).

### Discussion points
- Most ML jobs will be in **productionizing** ML. Most companies can't afford pure research.

## 1.5 ML systems vs. traditional software **[BOOK]**

- SWE separates code and data; ML = code + data + artifacts.
- Must **test & version data** too (hard: large, what's a diff?).
- Not all samples equal: 1M normal lungs + 1000 cancerous → a cancerous scan is worth far more.
- **Data poisoning** risk if you accept all data (backdoor attacks on face recognition).
- Model size: hundreds of millions–billions of params (GBs RAM); edge deployment is hard. BERT-large 340M params/1.35GB → within 2 yrs used in nearly all Google English searches.
- Monitoring/debugging hard (lack of visibility).

## 1.6 Designing ML systems **[BOOK]**

**ML systems design** = defining interface, algorithms, data, infrastructure, hardware to satisfy requirements.

### Four core requirements
1. **Reliable** — correct at desired performance despite hardware/software faults and human error. "Correct" is hard to know without labels → **fail silently** (Google Translate to unfamiliar language).
2. **Scalable** — grows in **complexity**, **traffic volume**, and **model count** (8000 models for 8000 customers). Resource scaling (up/down; **autoscaling** — Amazon Prime Day failure cost ~$72–99M/hr) + **artifact management** (1 model manual vs 100 automated, reproducibility).
3. **Maintainable** — many contributors (ML eng, DevOps, SMEs) using their preferred tools; blameless collaboration.
4. **Adaptable** — discover improvement opportunities, update without service interruption.

### Iterative process (six steps)
1. **Project scoping** (goals, objectives, constraints, stakeholders, resources)
2. **Data engineering** (Ch.2, 3)
3. **ML model development** (features Ch.4, model Ch.5)
4. **Deployment** (Ch.6)
5. **Monitoring & continual learning** (Ch.7, 8)
6. **Business analysis** (evaluate vs business goals; kill or scope new projects)

Example ad-model loop: choose metric → collect data → features → train → error analysis finds label errors → relabel → retrain → 99.99% no-show imbalance → collect more positives → retrain → degrades on yesterday's data → collect recent → deploy → revenue drops (impressions ≠ clicks) → switch metric to CTR → restart.

**[+] Scoping checklist**
```
□ Business objective & metric (revenue, cost, risk)   □ ML framing: inputs, outputs, loss
□ Non-ML baseline exists?                              □ Must-have vs nice-to-have (latency, accuracy, fairness)
□ Data availability, labels, privacy/regulation        □ Feedback loop length / natural labels
□ Cost of wrong predictions                            □ Who owns deployment, monitoring, on-call
□ Success threshold that justifies ML complexity       □ Retraining cadence & budget
```

---

# PART 2 — Chapter 2: Data Engineering Fundamentals

## 2.1 Data sources **[BOOK]**

| Source | Notes |
|---|---|
| **User input** | Malformatted (too long/short, wrong type/format). Needs heavy validation, fast processing (users impatient) |
| **System-generated** | Logs (state, events, jobs, predictions). Rarely malformed; can be processed periodically; but detect anomalies fast. Log everything → volume explosion → use log tools (Logstash, DataDog) + retention + cold storage (S3 Glacier ~5× cheaper than Standard) |
| **User behavior data** | Clicks, scrolls, dwell; system-generated but *user data* → privacy regulations |
| **Internal databases** | Inventory, CRM, users; used with predictions (e.g., check availability after search intent model) |
| **Third-party data** | 1st-party (your own), 2nd-party (another firm's customers, paid), 3rd-party (public not your customers). IDFA/AAID; Apple opt-in 2021 cut availability; China's CAID workaround |

**[CODE] Input validation at the boundary (pydantic)**
```python
from pydantic import BaseModel, Field, field_validator

class PredictRequest(BaseModel):
    user_id: str
    text: str = Field(min_length=1, max_length=5000)
    age: int | None = Field(default=None, ge=0, le=120)

    @field_validator("text")
    @classmethod
    def strip(cls, v): return v.strip()
```

## 2.2 Data formats **[BOOK]**

**Serialization** = converting data structures to storable/transmittable form. Consider: human-readability, access pattern, text vs binary.

| Format | Type | Human-readable | Used in |
|---|---|---|---|
| JSON | text | yes | everywhere |
| CSV | text (row-major) | yes | everywhere |
| Parquet | binary (column-major) | no | Hadoop, Redshift |
| Avro | binary (row) | no | Hadoop |
| Protobuf | binary | no | Google, TF (TFRecord) |
| Pickle | binary | no | Python/PyTorch |

- **JSON:** flexible key-value; structured or free text; schema changes painful; verbose.
- **Row-major (CSV)**: rows contiguous → fast row access & **fast writes**. **Column-major (Parquet)**: columns contiguous → fast column reads (select 4 of 1000 features), better caching, compression.
- CSV critique: float precision loss, poor non-text serialization.
- **Pandas is column-major** (DataFrame), NumPy default row-major. Iterating DataFrame by row: 2.41s vs by column 0.07s; convert to ndarray → row access fast.
- **Text vs binary:** 1,000,000 as text = 7 bytes; as int32 = 4 bytes. Example: CSV 14MB → Parquet 6MB. AWS: Parquet up to 2× faster to unload, 6× less storage vs text.

**[CODE] Format benchmark & column pruning**
```python
import pandas as pd, numpy as np, time
df = pd.DataFrame(np.random.rand(1_000_000, 50), columns=[f"f{i}" for i in range(50)])
df.to_csv("x.csv", index=False); df.to_parquet("x.parquet", compression="zstd")

t=time.time(); pd.read_csv("x.csv", usecols=["f1","f2"]); print("csv", time.time()-t)
t=time.time(); pd.read_parquet("x.parquet", columns=["f1","f2"]); print("parquet", time.time()-t)

# Row iteration: don't do this on DataFrames
arr = df.to_numpy()          # row access fast now
```

## 2.3 Data models **[BOOK]**

**Data model** = how data is represented (affects what problems are easy).

### Relational model
- Codd, 1970. Data = **relations** (sets of tuples); table = visual form; rows/columns unordered.
- **Normalization** (1NF, 2NF…) reduces redundancy, improves integrity (Book table split into Book + Publisher). Downside: joins expensive.
- **SQL** is **declarative** (say *what*), Python is **imperative** (say *how*). **Query optimizer** decides the plan (can be ML-improved: Neo). SQL is Turing complete with extensions; monster 700-line queries exist.
- SQL deviates from pure relational (duplicates allowed).

### Declarative ML **[BOOK]**
- Ludwig (Uber), H2O AutoML: declare features + task; system picks model. Abstracts away easy part; hard parts remain (feature eng, data processing, eval, shift detection, continual learning).

### NoSQL ("Not Only SQL")
- **Document model:** self-contained docs (JSON/XML/BSON), unique key, flexible schema per doc, better **locality**, harder joins. "Schemaless" is misleading: the **reader** assumes structure (schema-on-read).
- **Graph model:** nodes + edges; relationships first-class; fast relationship traversal (find everyone born in USA, variable hops). Hard in SQL/document DBs.
- PostgreSQL, MySQL support both relational and document.

### Structured vs unstructured
| Structured | Unstructured |
|---|---|
| Schema defined | No schema required |
| Easy to analyze | Fast arrival |
| Schema change painful (null age → 0 bug!) | Reader shoulders schema burden |
| **Data warehouse** | **Data lake** |
- Real bug: schema replaced null ages with 0 → model thought 0-year-olds; fix: -1.
- Distinction is fluid: **who assumes the structure — writer or reader**.

## 2.4 Storage engines & processing **[BOOK]**

### OLTP vs OLAP
- **Transaction** = any online action (order, tweet, upload). **OLTP**: insert/update/delete; **low latency, high availability**; row-major.
- **ACID:** Atomicity (all-or-nothing), Consistency (rules obeyed), Isolation (concurrent = as if serial), Durability (committed = permanent). **BASE** (Basically Available, Soft state, Eventual consistency) is the vague alternative.
- **OLAP**: analytical queries aggregating columns (avg price of rides in Sept in SF); column-major.
- OLTP/OLAP terms are **outdated**: (1) convergence (CockroachDB does analytics; DuckDB etc.), (2) **decoupling storage from compute** (BigQuery, Snowflake), (3) "online" is overloaded (online/nearline/offline).

### ETL / ELT
- **ETL:** Extract (validate, reject bad, notify sources) → Transform (join, clean, standardize e.g. M/F/1/2, dedupe, aggregate, derive features) → Load (how/how often to file/DB/warehouse).
- **ELT:** dump raw into lake, transform later → fast arrival but inefficient search at scale.
- **Lakehouse** (Databricks, Snowflake) merges lake flexibility with warehouse management.

**[CODE] Idempotent ETL with validation + dead-letter**
```python
import pandas as pd, pandera as pa
from pathlib import Path

schema = pa.DataFrameSchema({
    "user_id": pa.Column(str, nullable=False),
    "age": pa.Column(int, pa.Check.in_range(0, 120), nullable=True),
    "gender": pa.Column(str, pa.Check.isin(["M", "F", "O"]), coerce=True),
    "event_ts": pa.Column("datetime64[ns]"),
})
GENDER_MAP = {"Male":"M","Female":"F","1":"M","2":"F"}

def extract(path): return pd.read_json(path, lines=True)

def transform(df):
    df = df.copy()
    df["gender"] = df["gender"].astype(str).replace(GENDER_MAP)
    df = df.drop_duplicates(["user_id","event_ts"])
    return df

def load(df, out_dir, ds):               # partition by date => idempotent overwrite
    p = Path(out_dir)/f"ds={ds}"; p.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p/"part.parquet", index=False)

def run(path, out_dir, ds):
    raw = extract(path)
    try:
        good = schema.validate(transform(raw), lazy=True)
    except pa.errors.SchemaErrors as e:
        e.failure_cases.to_parquet(f"{out_dir}/rejects_{ds}.parquet")  # dead-letter
        bad_idx = e.failure_cases["index"].dropna().unique()
        good = transform(raw).drop(index=bad_idx, errors="ignore")
    load(good, out_dir, ds)
```

## 2.5 Modes of dataflow **[BOOK]**

1. **Through databases** — simplest; fails if processes can't share DB (different companies) or latency-strict (DB reads/writes slow).
2. **Through services** (request-driven; REST/RPC; microservices) — e.g. price service requests supply (driver) & demand (ride) predictions. REST for public APIs; RPC (looks like function call) for internal. Synchronous, tightly coupled → if a service is down, dependents fail/time out; N services → O(N²) connections.
3. **Through real-time transport** (event-driven; broker) — in-memory storage; events published to broker. **Pub/sub** (Kafka, Kinesis; topics; retention e.g. 7 days) vs **message queue** (RocketMQ, RabbitMQ; messages with intended consumers). Request-driven suits logic-heavy systems; event-driven suits data-heavy ones.

**[CODE] Kafka producer / consumer (confluent-kafka)**
```python
from confluent_kafka import Producer, Consumer
import json

p = Producer({"bootstrap.servers": "kafka:9092", "enable.idempotence": True, "acks": "all"})
def publish_prediction(topic, key, payload: dict):
    p.produce(topic, key=key, value=json.dumps(payload).encode(),
              on_delivery=lambda err, msg: err and print("FAILED", err))
    p.poll(0)

c = Consumer({"bootstrap.servers": "kafka:9092", "group.id": "price-svc",
              "auto.offset.reset": "latest", "enable.auto.commit": False})
c.subscribe(["driver_predictions", "ride_predictions"])
state = {}
while True:
    msg = c.poll(1.0)
    if msg is None or msg.error(): continue
    state[msg.topic()] = json.loads(msg.value())   # latest predicted supply/demand
    c.commit(msg)
```
**[+]** Use *at-least-once + idempotent consumers*; schema registry (Avro/Protobuf) for event contracts; dead-letter topics.

## 2.6 Batch vs stream processing **[BOOK]**

- **Historical data** (in storage) → **batch jobs** (MapReduce, Spark). **Streaming data** (in Kafka/Kinesis) → **stream processing** (Flink, KSQL, Spark Streaming).
- Stream can be low-latency (no DB write first). Strength = **stateful computation**: 30-day trial engagement — batch recomputes 30 days daily; stream updates incrementally.
- **Batch features / static features** (driver join date, rating) vs **streaming features / dynamic features** (drivers available now, rides in last 2 min, avg price of last 10 rides).
- Need infrastructure to compute both and **join** them for models.
- Batch is a special case of stream (Flink's argument); easier to make streaming do batch than reverse.

**[CODE] Stateful windowed feature (pure Python; conceptual Flink/Faust equivalent)**
```python
from collections import deque
import time

class SlidingWindowMean:
    def __init__(self, window_s): self.w, self.q, self.s = window_s, deque(), 0.0
    def add(self, x, ts=None):
        ts = ts or time.time(); self.q.append((ts, x)); self.s += x
        while self.q and self.q[0][0] < ts - self.w:
            _, old = self.q.popleft(); self.s -= old
    @property
    def mean(self): return self.s / len(self.q) if self.q else float("nan")
```

**[+] Feature-parity rule:** *One feature definition, executed in both training (batch) and serving (stream/online).* Use a feature store (Feast, Tecton, Vertex, SageMaker FS) with offline store (Parquet/warehouse) + online store (Redis/DynamoDB) and **point-in-time correct joins** to avoid leakage.

---

# PART 3 — Chapter 3: Training Data

## 3.1 Sampling **[BOOK]**

Sampling occurs when: selecting training data from the world, splitting train/val/test, subsampling for experiments, or sampling events for monitoring. Needed when you lack access to all data or compute/time/cost is limited. Quick experiments on subsets (but large models may behave differently at small scale).

### Non-probability sampling (biased, but convenient)
- **Convenience** (availability), **Snowball** (samples select next samples, e.g. followers of accounts), **Judgment** (experts choose), **Quota** (fixed quota per slice, e.g. 100 per age group, no randomization).
- Riddled with **selection bias** (Heckman). Real cases: LMs trained on Wikipedia/CommonCrawl/Reddit; sentiment from IMDB/Amazon reviews (only people who post reviews); self-driving data mostly Phoenix + Bay Area (sunny) → Waymo expanded to Kirkland, WA for rain.

### Random sampling
- **Simple random:** equal probabilities. Easy; rare classes may vanish (0.01% class at 1% sample).
- **Stratified:** sample each group separately (e.g. 1% of A and 1% of B). Not feasible when groups overlap (multilabel).
- **Weighted:** weights = selection probabilities; encode domain knowledge (recent data heavier) or correct distribution mismatch (25% red/75% blue but real 50/50 → red weight 3×). Different from **sample weights** (affect loss, shift decision boundary).
- **Importance sampling:** sample from proposal Q(x) instead of hard P(x), reweight by P(x)/Q(x); requires Q(x)>0 wherever P(x)≠0.
  E_P[x] = Σ P(x)x = Σ Q(x)x·P(x)/Q(x) = E_Q[x·P(x)/Q(x)]. Used in policy-based RL (reuse old policy rewards).
- **Reservoir sampling:** for unbounded streams; keep k items; for nth item pick random i∈[1,n]; if i≤k replace i-th slot. Each item has k/n probability; can stop anytime with correct probabilities.

**[CODE] Sampling toolkit**
```python
import random, numpy as np
from sklearn.model_selection import train_test_split, StratifiedShuffleSplit

# Weighted
random.choices([1,2,3,4,100,1000], weights=[.2,.2,.2,.2,.1,.1], k=2)

# Stratified split (preserves rare class)
Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, stratify=y, random_state=42)

# Reservoir sampling (correct: 1-indexed n)
def reservoir(stream, k, rng=random.Random(0)):
    res = []
    for n, x in enumerate(stream, 1):
        if n <= k: res.append(x)
        else:
            i = rng.randint(1, n)
            if i <= k: res[i-1] = x
    return res

# Importance sampling estimate of E_P[f(x)] using samples from Q
def importance_estimate(f, p_pdf, q_pdf, q_sample, n=100_000):
    x = q_sample(n)
    w = p_pdf(x) / q_pdf(x)
    return np.mean(w * f(x)), np.sum(w)**2/np.sum(w**2)   # estimate, effective sample size
```

## 3.2 Labeling **[BOOK]**

- Most production ML is supervised. **Natural labels** (click/no-click) vs hand labels. Labeling is a *core function* (Karpathy: "How long do we need an engineering team for?").

### Hand labels — problems
1. **Expensive** (radiologists vs crowd workers)
2. **Privacy** (someone looks at your data; may need on-prem annotators)
3. **Slow** (phonetic transcription 400× audio duration; lung study waited ~1 year) → slow iteration; adding a class (NEGATIVE/POSITIVE → +ANGRY) needs relabeling.

### Label multiplicity
- Multiple annotators/sources disagree (Star Wars NER example: 3, 6, 4 entities). More expertise → *more* disagreement. What is "human-level"?
- Fix: **clear problem definition** (e.g. "pick longest entity substring") + **train annotators** on it.

### Data lineage
- Track origin of each sample and label. Example: +1M crowd-labeled samples *reduced* performance due to worse annotators; if mixed without lineage you can't undo. Lineage flags biases and helps debugging.

**[CODE] Lineage + annotator agreement**
```python
import pandas as pd
from sklearn.metrics import cohen_kappa_score

df["source"] = "vendorA_2026Q3"; df["annotator_id"] = ...; df["label_version"] = "v3"
df["ingested_at"] = pd.Timestamp.utcnow()

k = cohen_kappa_score(df.loc[df.annotator_id=="a1","label"],
                      df.loc[df.annotator_id=="a2","label"])   # <0.6 => fix guidelines
# Slice error rate by source to detect a bad batch
(df.assign(err=df.pred!=df.label).groupby("source").err.mean())
```

## 3.3 Handling lack of hand labels **[BOOK]**

| Method | How | Ground truth needed? |
|---|---|---|
| **Weak supervision** | Noisy heuristics (labeling functions) | No, but small set to guide/evaluate |
| **Semi-supervision** | Structural assumptions expand small seed set | Yes (seed) |
| **Transfer learning** | Reuse pretrained model | No (zero-shot), Yes (fine-tune, fewer) |
| **Active learning** | Label the most useful samples | Yes |

### Weak supervision (Snorkel)
- **Labeling function (LF)**: encodes heuristic — keyword, regex, DB lookup, other model output. LFs are noisy, overlap, conflict → **combine, denoise, reweight** (label model). Also called **programmatic labeling**.
- Benefits: cost (expertise versioned/shared), privacy (write LFs on cleared subset), speed (1K→1M), adaptivity (reapply LFs).
- Study: radiologist wrote LFs for 8 hours ≈ ~1 year of hand labels; models improved with more unlabeled data; 6 LFs reused between CXR and EXR tasks.
- Still train ML: LFs don't cover all samples; the model generalizes.

### Semi-supervision
- **Self-training:** train → predict unlabeled → add high-confidence predictions → retrain.
- **Similarity-based:** same tweet/profile hashtags share topic; clustering/KNN.
- **Perturbation-based:** small perturbations don't change label.
- Caution: how much of scarce labels to hold out for validation.

### Transfer learning
- Pretrain on cheap abundant task (language modeling: predict next token) → **downstream task**; zero-shot, fine-tune (whole or part), or prompting via templates. Larger base models → better downstream; training costs tens of millions USD → few companies pretrain; rest fine-tune.

### Active learning (query learning)
- Model picks samples to label. Heuristics: **uncertainty measurement** (lowest confidence), **query-by-committee** (max disagreement), highest gradient/loss reduction. Sources: synthesized, pool-based, stream-based (most exciting for production).

**[CODE] Labeling functions + label model (Snorkel-style)**
```python
import numpy as np
ABSTAIN, NOT_EM, EM = -1, 0, 1

def lf_pneumonia(note): return EM if "pneumonia" in note.lower() else ABSTAIN
def lf_regex_sob(note):
    import re; return EM if re.search(r"shortness of breath|dyspnea", note, re.I) else ABSTAIN
def lf_routine(note): return NOT_EM if "routine checkup" in note.lower() else ABSTAIN
LFS = [lf_pneumonia, lf_regex_sob, lf_routine]

def apply_lfs(notes): return np.array([[lf(n) for lf in LFS] for n in notes])
L = apply_lfs(notes)

# Simplest denoiser: majority vote ignoring abstains
def majority(L):
    out = []
    for row in L:
        v = row[row != ABSTAIN]
        out.append(np.bincount(v).argmax() if len(v) else ABSTAIN)
    return np.array(out)

# Real thing (learns LF accuracies):
# from snorkel.labeling.model import LabelModel
# lm = LabelModel(cardinality=2); lm.fit(L_train=L, n_epochs=500, seed=123)
# probs = lm.predict_proba(L)     # -> train end model on confident probs
```

**[CODE] Self-training & uncertainty-based active learning**
```python
import numpy as np
from sklearn.base import clone

def self_train(model, X_l, y_l, X_u, thr=0.95, rounds=5):
    for _ in range(rounds):
        m = clone(model).fit(X_l, y_l)
        p = m.predict_proba(X_u); conf = p.max(1); pick = conf >= thr
        if not pick.any(): break
        X_l = np.vstack([X_l, X_u[pick]]); y_l = np.concatenate([y_l, p[pick].argmax(1)])
        X_u = X_u[~pick]
    return clone(model).fit(X_l, y_l)

def uncertainty_sample(model, X_pool, k=100):          # least-confident
    p = model.predict_proba(X_pool)
    return np.argsort(p.max(1))[:k]                    # send these to annotators

def entropy_sample(model, X_pool, k=100):
    p = model.predict_proba(X_pool); ent = -(p*np.log(p+1e-12)).sum(1)
    return np.argsort(-ent)[:k]
```

## 3.4 Class imbalance **[BOOK]**

- **Definition:** big difference in class counts (99.99% normal X-rays). Also in regression (skewed healthcare bills; predicting the 95th percentile accurately matters more).
- **Why hard:**
  1. Insufficient signal for minority (few-shot; or class unseen).
  2. **Trivial heuristic** (always majority = 99.99% accuracy) hard for gradient descent to escape.
  3. **Asymmetric error costs** (missing cancer ≫ false alarm).
- **Causes:** inherent (fraud: 6.8¢ per $100; churn; disease screening; resume screening 98% rejected; object detection boxes), **sampling bias** (spam filtered before reaching DB; 85% of email is spam), **labeling errors**.
- Sensitivity to imbalance grows with problem complexity; linearly separable problems are unaffected; binary easier than multiclass; very deep networks handle imbalance better.
- Arguably a good model should learn the real distribution — but techniques are still often required.

### 1) Right metrics
- Accuracy is misleading. Example (1000 samples, 100 cancer):

| | TP | FP | FN | TN | Acc | Prec | Recall | F1 |
|---|---|---|---|---|---|---|---|---|
| Model A | 10 | 10 | 90 | 890 | 0.9 | 0.5 | 0.1 | **0.17** |
| Model B | 90 | 90 | 10 | 810 | 0.9 | 0.5 | 0.9 | **0.64** |

- **F1, recall are asymmetric** (depend on which class is positive; A's F1 = 0.17 for CANCER, 0.95 for NORMAL). Multiclass: F1 per class.
- **ROC curve** (TPR vs FPR across thresholds), **AUC**. ROC ignores negative-class performance; for heavy imbalance prefer **Precision-Recall curve** (Davis & Goadrich).

### 2) Data-level: resampling
- **Oversample** minority (risk: overfit to copies), **undersample** majority (risk: lose info).
- **Tomek links** (remove majority sample in close opposite-class pairs), **SMOTE** (convex combos of minority points) — only proven for low dimensions. Near-Miss, one-sided selection need distances.
- **Never evaluate on resampled data.**
- **Two-phase learning:** train on resampled, fine-tune on original. **Dynamic sampling:** oversample low performers during training.

### 3) Algorithm-level: modify the loss
- **Cost-sensitive learning** (Elkan 2001): cost matrix C_ij; L(x)=Σ_j C_ij·P(j|x). Cost matrix must be hand-defined.
- **Class-balanced loss:** W_i = N / n_i (inverse frequency); or effective number of samples (Cui et al. 2019).
- **Focal loss:** down-weights easy examples by (1−p_t)^γ.
- Ensembles help but aren't primarily for imbalance.

**[CODE] Imbalance toolkit**
```python
import numpy as np, torch, torch.nn as nn, torch.nn.functional as F
from sklearn.metrics import (classification_report, precision_recall_curve,
                             average_precision_score, roc_auc_score)
from sklearn.linear_model import LogisticRegression
from imblearn.over_sampling import SMOTE
from imblearn.pipeline import Pipeline as ImbPipeline   # resample ONLY train folds

# 1. Metrics — always PR-AUC + per-class
print(classification_report(y_te, pred, digits=3))
print("AP", average_precision_score(y_te, proba), "ROC-AUC", roc_auc_score(y_te, proba))

# 2. Threshold tuning for a recall target
p, r, t = precision_recall_curve(y_te, proba)
idx = np.where(r[:-1] >= 0.90)[0][-1]      # highest precision with recall>=0.9
threshold = t[idx]

# 3. Class weights (algorithm-level)
clf = LogisticRegression(class_weight="balanced", max_iter=1000).fit(Xtr, ytr)

# 4. SMOTE inside a pipeline (no leakage into validation folds)
pipe = ImbPipeline([("smote", SMOTE(random_state=0)), ("clf", LogisticRegression(max_iter=1000))])

# 5. Focal loss (multiclass)
class FocalLoss(nn.Module):
    def __init__(self, gamma=2.0, alpha=None):
        super().__init__(); self.gamma, self.alpha = gamma, alpha   # alpha: tensor of class weights
    def forward(self, logits, target):
        logp = F.log_softmax(logits, -1)
        logpt = logp.gather(1, target[:, None]).squeeze(1)
        pt = logpt.exp()
        loss = -((1 - pt) ** self.gamma) * logpt
        if self.alpha is not None: loss = loss * self.alpha[target]
        return loss.mean()

# 6. Class-balanced weights
counts = np.bincount(y_train); w = torch.tensor(len(y_train)/(len(counts)*counts), dtype=torch.float)
criterion = nn.CrossEntropyLoss(weight=w)

# 7. Cost-sensitive decision from probabilities
C = np.array([[0, 1], [10, 0]])              # C[i,j]: true i predicted j; FN costs 10x FP
def cost_decision(proba):                    # proba shape (n, 2)
    expected = proba @ C.T                   # expected cost of predicting each class
    return expected.argmin(1)
```

## 3.5 Data augmentation **[BOOK]**

Increase data; makes models robust to noise/adversarial attacks. Depends on modality.

1. **Simple label-preserving transforms**
   - CV: crop, flip, rotate, invert, erase (AlexNet: "computationally free" on CPU while GPU trains).
   - NLP: replace words with synonyms/nearest embeddings ("happy"→"glad").
2. **Perturbation** — add noise. Neural nets are noise-sensitive: changing **one pixel** misclassified 67.97% of CIFAR-10 and 16.04% of ImageNet test images (Su et al.). **Adversarial attacks**; **DeepFool** finds minimal noise; **adversarial augmentation** trains on such samples. NLP: BERT masking picks 15% of tokens, replaces 10% of those with random words (=1.5% of tokens).
3. **Data synthesis**
   - NLP **templates** ("Find me a [CUISINE] restaurant within [NUMBER] miles of [LOCATION]").
   - CV **mixup**: x = γx₁ + (1−γ)x₂, y = γy₁ + (1−γ)y₂ → better generalization, robustness to corrupt labels/adversarial examples, stabilizes GAN training.
   - GAN-generated data (CycleGAN improved CT segmentation).

**[CODE]**
```python
import numpy as np, torch, random, itertools
import torchvision.transforms as T

img_aug = T.Compose([T.RandomResizedCrop(224, scale=(0.7,1.0)), T.RandomHorizontalFlip(),
                     T.ColorJitter(0.2,0.2,0.2), T.ToTensor()])

def mixup(x, y_onehot, alpha=0.4):
    lam = np.random.beta(alpha, alpha); idx = torch.randperm(x.size(0))
    return lam*x + (1-lam)*x[idx], lam*y_onehot + (1-lam)*y_onehot[idx]

def synonym_swap(tokens, synonyms, p=0.15):
    return [random.choice(synonyms[t]) if t in synonyms and random.random()<p else t for t in tokens]

def template_synth(tpl, **slots):
    keys = list(slots)
    for combo in itertools.product(*slots.values()):
        yield tpl.format(**dict(zip(keys, combo)))
list(template_synth("Find me a {c} restaurant within {n} miles of {loc}",
                    c=["Thai","Mexican"], n=[2,5], loc=["my office","home"]))

def bert_mask(tokens, vocab, p=0.15):
    out = tokens[:]
    for i in range(len(out)):
        if random.random() < p:
            r = random.random()
            out[i] = "[MASK]" if r < 0.8 else random.choice(vocab) if r < 0.9 else out[i]
    return out
```
**[+]** Always augment **only the training split**; validate augmentations don't change labels (a flipped "6" is a "9"). Use TTA (test-time augmentation) as a cheap ensemble.

---

# PART 4 — Chapter 4: Feature Engineering

## 4.1 Learned vs engineered features **[BOOK]**

- Facebook 2014: right features are the most important thing; features often beat hyperparameter tuning.
- Deep learning = "feature learning" but: most production ML isn't DL; you still need non-text/image features.
- **Classical NLP pipeline:** lemmatize, expand contractions, strip punctuation, lowercase, **n-grams** → vocabulary → count vectors ("I like food" 1-/2-grams: [I, like, food, I like, like food]).
- DL: split words, one-hot/embeddings; model learns features.
- Extra features still needed: comment (poster, votes), user (account age, post rate), thread (views). TikTok: millions of features; fraud: subject-matter expertise.

**[CODE] N-gram baseline (production-safe)**
```python
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
pipe = make_pipeline(TfidfVectorizer(ngram_range=(1,2), min_df=3, sublinear_tf=True),
                     LogisticRegression(max_iter=2000, class_weight="balanced"))
```

## 4.2 Common operations **[BOOK]**

### Handling missing values
Three types:
- **MNAR** (missing because of the value: high earners hide income)
- **MAR** (missing due to another observed variable: gender A hides age)
- **MCAR** (no pattern; rare — investigate before assuming)

**Deletion**
- *Column deletion:* loses info (marital status >50% missing yet predictive).
- *Row deletion:* OK only for MCAR with tiny fraction (<0.1%); dangerous for MNAR (missingness is signal) and MAR (removes whole groups → bias).

**Imputation**
- Defaults (empty string), mean/median/mode (median July temperature).
- Don't impute with *possible* values (children=0 conflates unknown with none); age=0 bug when front-end stopped asking.
- Risks: noise, **data leakage** (use train statistics only). No perfect method.

**[CODE] Leakage-safe preprocessing pipeline**
```python
import numpy as np, pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler, OneHotEncoder, KBinsDiscretizer, FunctionTransformer

num = ["age", "annual_income", "n_children"]
cat = ["gender", "marital_status", "job"]

num_pipe = Pipeline([
    ("impute", SimpleImputer(strategy="median", add_indicator=True)),   # keeps "was missing" signal (MNAR!)
    ("log", FunctionTransformer(np.log1p, feature_names_out="one-to-one")),
    ("scale", StandardScaler()),
])
cat_pipe = Pipeline([
    ("impute", SimpleImputer(strategy="constant", fill_value="__MISSING__")),
    ("ohe", OneHotEncoder(handle_unknown="ignore", min_frequency=20)),   # unseen -> zeros/infrequent bucket
])
pre = ColumnTransformer([("num", num_pipe, num), ("cat", cat_pipe, cat)])
# pre.fit(train) ONLY; serialize `pre` with the model so serving == training
```

### Scaling
- Features in wildly different ranges (age 20–40 vs income 10k–150k) mislead models; scaling often gives a big boost (author: ~10%).
- **Min-max** to [0,1]: x' = (x−min)/(max−min); to [a,b]: x' = a + (x−min)(b−a)/(max−min) ([−1,1] often better empirically).
- **Standardization**: x' = (x−μ)/σ (assume roughly normal).
- **Log transform** for skew (caution: analyses on log-transformed data ≠ original scale).
- Scaling needs **global statistics** (from train); reuse at inference; if new data drifts, stats are stale → retrain often. Common leakage source.

### Discretization (quantization)
- Continuous → buckets (income: <35k, 35–100k, >100k; age bins). 9000.50 ≈ 10000. Works for discrete features too. Choose boundaries by histograms, quantiles, SME.

### Encoding categorical features
- Categories **aren't static in production** (Amazon: 2M+ brands, new ones daily).
- Naive integer ID crashes on unseen; "UNKNOWN" bucket → never recommended (unseen in train); top-99% + UNKNOWN treats new luxury/knockoff/established brands alike.
- **Hashing trick** (Vowpal Wabbit): hash(category) mod 2^k; fixed size, handles unseen; collisions random (Booking.com: 50% collisions → <0.5% log-loss loss); tune hash space; can use locality-sensitive hashing. In scikit-learn, TensorFlow, gensim; great for continual learning.

### Feature crossing
- Combine features to model non-linear interactions (marital status × children). Essential for linear/logistic/tree models; helpful for NNs (DeepFM, xDeepFM). Caveats: feature space explosion (100×100=10,000), overfitting.

### Positional embeddings
- Transformers process tokens in parallel → need position info. Raw 0..7 not unit-variance; rescaled 0..1 differences too small.
- **Learned** position embeddings (matrix with #positions columns; summed with word embeddings; BERT/HF).
- **Fixed** (sine for even indices, cosine odd) = special case of **Fourier features** — works for continuous coordinates (3D teapot). γ(v)=[a₁cos(2πb₁ᵀv), a₁sin(2πb₁ᵀv), …].

**[CODE]**
```python
import numpy as np, hashlib
from sklearn.feature_extraction import FeatureHasher

# Hashing trick: stable across processes (don't use Python's hash(): it's salted!)
def stable_hash(s, n_bits=18): return int(hashlib.md5(s.encode()).hexdigest(), 16) % (1 << n_bits)
hasher = FeatureHasher(n_features=2**18, input_type="string", alternate_sign=False)
X = hasher.transform([[f"brand={b}", f"cat={c}"] for b, c in zip(brands, cats)])

# Feature cross
df["marital_x_children"] = df.marital_status.astype(str) + "_" + df.n_children.astype(str)

# Quantile discretization
from sklearn.preprocessing import KBinsDiscretizer
kb = KBinsDiscretizer(n_bins=5, encode="ordinal", strategy="quantile")

# Fixed sinusoidal position encoding (Vaswani)
def sinusoidal_pe(n_pos, d):
    pos = np.arange(n_pos)[:, None]; i = np.arange(d)[None, :]
    angle = pos / np.power(10000, (2*(i//2))/d)
    pe = np.zeros((n_pos, d)); pe[:, 0::2] = np.sin(angle[:, 0::2]); pe[:, 1::2] = np.cos(angle[:, 1::2])
    return pe

# Fourier features for continuous coordinates
def fourier_features(v, B):                 # v:(n,d), B:(d,m) ~ N(0, sigma^2)
    proj = 2*np.pi * v @ B
    return np.concatenate([np.cos(proj), np.sin(proj)], axis=1)
```
**[+]** Other must-know encodings: target/mean encoding (with out-of-fold to avoid leakage), frequency encoding, learned embeddings for high-cardinality IDs, cyclical encoding for hour/day (sin/cos).

## 4.3 Data leakage **[BOOK]**

**Definition:** a form of the label leaks into features, and that information isn't available at inference. Symptom: great test performance, mysterious production failure.

- Example: hospital A sends suspicious patients to a different CT machine → model learns machine identity. Hospital B randomizes → fails.
- Kaggle Ion Switching: winners exploited a leak.

### Six common causes
1. **Random split of time-correlated data** → split **by time** (train first 4 weeks; split week 5 into val/test). Stock market, music trends (artist death).
2. **Scaling before splitting** → split first, fit scaler on train only. (Some advise split before EDA.)
3. **Filling missing values with statistics from test/all data.**
4. **Duplicates across splits** (CIFAR-10: 3.3% and CIFAR-100: 10% of test images duplicated in train) → dedupe before *and* after splitting; oversample *after* splitting.
5. **Group leakage** (same patient's two scans in train and test) → group-aware split.
6. **Data collection process leakage** (scan machine) → know provenance; normalize across sources (same resolution).

### Detecting leakage
- Check **feature–label correlation**; unusually high → investigate. Combinations can leak (start date + end date → tenure).
- **Ablation studies** for important features.
- Watch **new features** giving suspiciously big gains.
- Use test split **only once**, for final reporting.

**[CODE] Leakage guards**
```python
import pandas as pd, numpy as np
from sklearn.model_selection import GroupShuffleSplit, TimeSeriesSplit, cross_val_score
from sklearn.metrics import roc_auc_score

# (1) Time split
def time_split(df, ts="ts", val_start="2026-09-01", test_start="2026-09-15"):
    tr = df[df[ts] < val_start]; va = df[(df[ts]>=val_start)&(df[ts]<test_start)]; te = df[df[ts]>=test_start]
    assert tr[ts].max() < va[ts].min() <= va[ts].max() < te[ts].min()
    return tr, va, te

# (4) Dedupe BEFORE split
df = df.drop_duplicates(subset=["text_norm"])

# (5) Group split
gss = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=0)
tr_idx, te_idx = next(gss.split(X, y, groups=df.patient_id))
assert set(df.patient_id.iloc[tr_idx]).isdisjoint(df.patient_id.iloc[te_idx])

# Time-series CV
tscv = TimeSeriesSplit(n_splits=5)

# Detect leakage: single-feature AUC scan
def single_feature_auc(df, y, cols):
    out = {}
    for c in cols:
        s = df[c].fillna(df[c].median()) if df[c].dtype.kind in "fi" else df[c].astype("category").cat.codes
        out[c] = max(roc_auc_score(y, s), 1 - roc_auc_score(y, s))
    return pd.Series(out).sort_values(ascending=False)   # >0.95 => suspicious

# Train/test overlap sanity test (put in CI)
def test_no_overlap(train_ids, test_ids): assert not (set(train_ids) & set(test_ids))
```

## 4.4 Engineering good features **[BOOK]**

- More features usually help but also: more leakage opportunities, overfitting, memory, inference latency (online extraction), **technical debt** (pipeline changes break dependent features). L1 theoretically zeros useless features but practically remove them. Store definitions to reuse (not all feature stores manage definitions).

### Feature importance
- XGBoost `get_score`, **SHAP** (global + per-prediction), InterpretML. Intuition: performance drop when feature removed. Facebook: top 10 features ≈ half of importance; bottom 300 <1%.

### Feature generalization
- IDs don't generalize (comment ID), user ID may.
- **Coverage:** % of examples with the feature; small coverage rarely useful *unless* missingness is informative (1% coverage, 99% positive → great). Coverage differing between train and test (90% vs 20%) → distribution mismatch/leakage.
- **Value distribution overlap:** DAY_OF_WEEK Mon–Sat in train, Sunday in test → hurts. HOUR_OF_DAY overlaps 100%.
- **Generalization vs specificity trade-off:** IS_RUSH_HOUR more general, less specific than HOUR_OF_DAY.

### Best-practice summary (book)
Split by time; oversample after splitting; scale/normalize after splitting; use train-only statistics (scaling + missing values); understand data generation, track lineage; know feature importance; use generalizing features; remove no-longer-useful features. Let non-engineers (SMEs) contribute.

**[CODE] Feature audit**
```python
import shap, numpy as np, pandas as pd
def coverage_report(train, test, cols):
    return pd.DataFrame({"train_cov": train[cols].notna().mean(), "test_cov": test[cols].notna().mean()}) \
             .assign(gap=lambda d: (d.train_cov-d.test_cov).abs()).sort_values("gap", ascending=False)

def value_overlap(train, test, col):
    a, b = set(train[col].dropna().unique()), set(test[col].dropna().unique())
    return len(a & b) / max(len(b), 1)      # <0.9 -> risky categorical feature

explainer = shap.TreeExplainer(model); sv = explainer(X_val)
shap.plots.bar(sv)                       # global importance
shap.plots.waterfall(sv[0])              # single prediction
# Ablation
base = score(model, X_val, y_val)
for f in top_features: print(f, base - score(retrain_without(f), X_val, y_val))
```

---

# PART 5 — Chapter 5: Model Development

## 5.1 Framing ML problems **[BOOK]**

- An ML problem = **inputs + outputs + objective function**. "Speed up support" ≠ ML problem. Bottleneck: routing → **classification** into 4 departments.
- **Task types:** classification (binary, multiclass, multilabel; high cardinality) vs regression; each convertible to the other (bucket prices; threshold scores).
- **Binary** simplest (F1, confusion matrix easy). **Multiclass** — high cardinality needs ≥~100 examples/class → **hierarchical classification**.
- **Multilabel**: two approaches — multi-hot vector [0,1,1,0], or N binary classifiers. Hardest in practice: annotation multiplicity, and how many labels to pick from probabilities [0.45, 0.2, 0.02, 0.33] (threshold?).
- **Framing matters:** next-app prediction as N-way classification breaks whenever an app is added; reframe as **regression/scoring per (user, context, app)** → new app = new input, no retrain.

### Objective functions
- Loss guides learning: RMSE/MAE (regression), log loss (binary), cross entropy (multiclass). Example: label [0,0,0,1], pred [0.45,0.2,0.02,0.33] → CE = −ln(0.33) ≈ 1.109.
- **Decoupling objectives:** combined loss α·quality_loss + β·engagement_loss requires retraining to change α, β (Pareto optimization to tune). Better: **train separate models, combine scores** α·quality + β·engagement (tweak without retraining). Also separate maintenance schedules (spam evolves faster than quality).

**[CODE]**
```python
import numpy as np
def cross_entropy(p, q): return -np.sum(np.array(p) * np.log(np.array(q) + 1e-12))
cross_entropy([0,0,0,1], [0.45,0.2,0.02,0.33])     # 1.1087

# Decoupled ranking: tune alpha/beta at serving time, no retrain
def rank(posts, quality_model, engagement_model, alpha=0.6, beta=0.4):
    q = quality_model.predict(posts); e = engagement_model.predict(posts)
    return posts[np.argsort(-(alpha*q + beta*e))]

# Multilabel: per-label thresholds (tune on validation)
from sklearn.multiclass import OneVsRestClassifier
ovr = OneVsRestClassifier(LogisticRegression(max_iter=1000)).fit(X, Y)   # Y multi-hot
proba = ovr.predict_proba(Xva); preds = proba >= thresholds[None, :]

# Scoring framing (new items need no retrain)
def score_apps(model, user_feats, ctx_feats, app_feats_list):
    return [model.predict_proba([[*user_feats, *ctx_feats, *a]])[0,1] for a in app_feats_list]
```

## 5.2 Evaluating & selecting models **[BOOK]**

- Classical ML isn't dead: collaborative filtering, matrix factorization, gradient-boosted trees (strict latency), and hybrids (classic features → NN).
- Choose from a set suitable for the task (toxic tweets: NB, LR, RNN, BERT/GPT; fraud: kNN, isolation forest, clustering, NN).
- Compare not just accuracy/F1/log-loss but data needs, compute, latency, **interpretability**.
- Keep up with NeurIPS/ICLR/ICML.

### Six tips
1. **Avoid the SOTA trap** — SOTA on static datasets ≠ fast/cheap/better on *your* data.
2. **Start with the simplest model** — easier to deploy (validates pipeline consistency), easier to debug, gives a baseline. Simplest ≠ least effort (pretrained BERT is easy to start).
3. **Avoid human biases** — equal experiment budgets per architecture (BERT vs GBT story: 100 experiments each).
4. **Good now vs good later** — **learning curves** (performance vs #samples) predict whether more data helps; tree now, NN later; CF vs online-learning NN (NN overtook CF after 2 weeks).
5. **Evaluate trade-offs** — FP vs FN (fingerprint unlock → fewer FP; covid screening → fewer FN), compute vs accuracy, interpretability vs performance.
6. **Understand assumptions** — prediction (Y predictable from X), **IID**, **smoothness**, **tractability** (P(Z|X) for generative), **boundaries** (linear classifier), **conditional independence** (Naive Bayes), **normality**.

**[CODE] Fair model bake-off**
```python
import numpy as np, optuna
from sklearn.model_selection import cross_val_score, learning_curve
from sklearn.dummy import DummyClassifier

baseline = cross_val_score(DummyClassifier(strategy="prior"), X, y, scoring="average_precision", cv=5).mean()

def make_objective(model_fn):
    def obj(trial):
        m = model_fn(trial)
        return cross_val_score(m, X, y, scoring="average_precision", cv=5, n_jobs=-1).mean()
    return obj

def lgbm_fn(t):
    import lightgbm as lgb
    return lgb.LGBMClassifier(n_estimators=t.suggest_int("n", 100, 800), learning_rate=t.suggest_float("lr", 1e-3, .3, log=True),
                              num_leaves=t.suggest_int("leaves", 8, 128), subsample=.8, colsample_bytree=.8, verbose=-1)
def lr_fn(t): return LogisticRegression(C=t.suggest_float("C", 1e-3, 100, log=True), max_iter=2000)

results = {}
for name, fn in {"lgbm": lgbm_fn, "logreg": lr_fn}.items():
    st = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=0))
    st.optimize(make_objective(fn), n_trials=50)      # SAME budget per candidate
    results[name] = st.best_value
print("baseline", baseline, results)

# Learning curve: will more data help?
sizes, tr, va = learning_curve(best_model, X, y, train_sizes=np.linspace(.1, 1, 8), cv=5, scoring="average_precision")
```

## 5.3 Ensembles **[BOOK]**

- Base learners; majority vote / average. 20/22 Kaggle winners (2021) use ensembles; SQuAD 2.0 top 20 all ensembles. Less used in prod (complexity) unless small gains = big money (CTR).
- **Why it works:** 3 uncorrelated classifiers at 70% → majority accuracy = 0.343 + 0.441 = **0.784**. Correlation kills gains → use diverse model types.
- **Bagging** (bootstrap aggregating): sample with replacement, train per bootstrap; vote/average; reduces variance; helps unstable methods (NN, trees), can mildly hurt stable ones (kNN). **Random forest** = bagging + feature randomness.
- **Boosting**: sequentially reweight misclassified samples; final = weighted combo. GBM, **XGBoost** (won many competitions; Higgs boson), **LightGBM** (distributed, faster on large data).
- **Stacking**: meta-learner combines base outputs (heuristic or model).
- Bagging/boosting + resampling help imbalance.

**[CODE]**
```python
from sklearn.ensemble import StackingClassifier, RandomForestClassifier, VotingClassifier
from sklearn.linear_model import LogisticRegression
import lightgbm as lgb
from sklearn.neural_network import MLPClassifier

stack = StackingClassifier(
    estimators=[("rf", RandomForestClassifier(300, n_jobs=-1)),
                ("gbm", lgb.LGBMClassifier(n_estimators=400, verbose=-1)),
                ("nn", MLPClassifier((64,), max_iter=300))],
    final_estimator=LogisticRegression(),
    cv=5, stack_method="predict_proba", n_jobs=-1)          # out-of-fold preds => no leakage into meta-learner

# Verify ensemble math
from math import comb
p=.7; print(sum(comb(3,k)*p**k*(1-p)**(3-k) for k in (2,3)))   # 0.784
```

## 5.4 Experiment tracking & versioning **[BOOK]**

- **Artifact** = file generated in an experiment (loss curves, logs, checkpoints).
- **Track:** loss curve (train + each eval split), metrics on non-test splits, speed (steps/s, tokens/s), system metrics (memory, CPU/GPU util), parameter/hyperparameter values over time (LR schedule, gradient norms, weight norms). Too many metrics can distract.
- Simple approach: auto-copy code + timestamped logs; better: W&B, MLflow, Neptune.
- **Versioning:** ML = code + data. Code diffs are line-based, data lines can be huge; duplicating datasets is infeasible; unclear what "diff" means (DVC: checksum change of the directory + files added/removed); merge conflicts don't make sense (dataset X + Y ≠ model-backed Z); **GDPR deletion** makes old versions unrecoverable.
- Versioning helps reproducibility but doesn't guarantee it (framework/hardware non-determinism: CUDA atomics).

**[CODE] Reproducible, tracked run (MLflow)**
```python
import mlflow, random, numpy as np, torch, subprocess, os, hashlib, json

def seed_everything(seed=42):
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed); torch.cuda.manual_seed_all(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    torch.backends.cudnn.deterministic = True; torch.backends.cudnn.benchmark = False

def file_hash(path): return hashlib.sha256(open(path,"rb").read()).hexdigest()[:12]

mlflow.set_experiment("churn-v2")
with mlflow.start_run(run_name="lgbm-baseline"):
    seed_everything(42)
    mlflow.set_tags({"git_sha": subprocess.check_output(["git","rev-parse","HEAD"]).decode().strip(),
                     "data_version": file_hash("train.parquet")})            # or `dvc` rev
    mlflow.log_params(params)
    for epoch in range(E):
        ...
        mlflow.log_metrics({"train_loss": tl, "val_loss": vl, "val_ap": ap}, step=epoch)
    mlflow.log_artifact("feature_importance.png")
    mlflow.sklearn.log_model(pipe, "model", registered_model_name="churn")     # registry
```
**[+]** Data versioning: DVC, lakeFS, Delta Lake/Iceberg time travel. Log the **exact query + snapshot id** of training data. Store model **cards** (intended use, metrics per slice, limits).

## 5.5 Debugging ML models **[BOOK]**

**Why hard:** (1) **fail silently**, (2) validating a fix is slow (retrain hours), (3) **cross-functional** (data, labels, features, algo, code, infra — different owners).

**Common causes:** theoretical constraints (linear on non-linear), poor implementation (forgot `torch.no_grad()`/`eval()`), poor hyperparameters, **data problems** (mismatched labels, stale normalization stats), poor features (too many/too few).

**Three techniques:**
1. **Start simple, add complexity gradually** (one RNN layer first; MLM before NSP). Cloning SOTA repos = hard to debug.
2. **Overfit a single batch** (10 images → 100% acc; 100 sentence pairs → BLEU≈100). If it can't, implementation bug.
3. **Set random seeds.**
(+ Karpathy's *A Recipe for Training Neural Networks*.)

**[CODE] Debug harness**
```python
import torch, torch.nn as nn

def overfit_single_batch(model, batch, steps=300, lr=1e-3):
    x, y = batch; opt = torch.optim.Adam(model.parameters(), lr=lr); lossf = nn.CrossEntropyLoss()
    model.train()
    for i in range(steps):
        opt.zero_grad(); loss = lossf(model(x), y); loss.backward(); opt.step()
        if i % 50 == 0: print(i, loss.item())
    acc = (model(x).argmax(1) == y).float().mean().item()
    assert acc > 0.99, f"cannot overfit one batch (acc={acc:.2f}) -> bug in model/loss/data"

def sanity_checks(model, loader, n_classes):
    x, y = next(iter(loader))
    init_loss = nn.CrossEntropyLoss()(model(x), y).item()
    print("expected init loss ~", torch.log(torch.tensor(float(n_classes))).item(), "got", init_loss)
    # gradient flow
    model.zero_grad(); nn.CrossEntropyLoss()(model(x), y).backward()
    for n, p in model.named_parameters():
        assert p.grad is not None and torch.isfinite(p.grad).all(), f"bad grad {n}"

@torch.no_grad()
def evaluate(model, loader):
    model.eval()                    # do NOT forget: dropout/batchnorm
    ...
```

## 5.6 Distributed training **[BOOK]**

- Data doesn't fit memory → out-of-memory preprocessing/shuffling/batching; large samples → small batches → unstable SGD; **gradient checkpointing** (memory-compute trade-off; >10× bigger models for ~20% extra compute).
- **Data parallelism:** split data across machines, accumulate gradients.
  - **Synchronous SGD**: waits for all workers → **stragglers** worsen with scale.
  - **Asynchronous SGD**: apply gradients as they arrive → **gradient staleness**; converges similarly when updates are sparse.
  - **Huge global batch**: learning-rate scaling limited; diminishing returns (GPT-3: 3.2M batch).
  - **Master-worker imbalance**: smaller batch on master.
- **Model parallelism:** components on different machines; layer-wise means sequential waiting → **pipeline parallelism** (micro-batches so machines overlap). Combine data + model parallelism.

**[CODE] PyTorch DDP + AMP + gradient checkpointing + accumulation**
```python
# torchrun --nproc_per_node=8 train.py
import os, torch, torch.distributed as dist
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.data import DataLoader, DistributedSampler
from torch.utils.checkpoint import checkpoint

dist.init_process_group("nccl"); rank = dist.get_rank(); local = int(os.environ["LOCAL_RANK"])
torch.cuda.set_device(local)
model = MyModel().cuda(local); model = DDP(model, device_ids=[local])

sampler = DistributedSampler(dataset, shuffle=True, seed=0)
loader = DataLoader(dataset, batch_size=64, sampler=sampler, num_workers=8, pin_memory=True, drop_last=True)
opt = torch.optim.AdamW(model.parameters(), lr=3e-4 * dist.get_world_size() ** 0.5)   # LR scaling heuristic
scaler = torch.cuda.amp.GradScaler(); ACCUM = 4

class Block(torch.nn.Module):
    def forward(self, x): return checkpoint(self.inner, x, use_reentrant=False)   # trade compute for memory

for epoch in range(E):
    sampler.set_epoch(epoch)                       # different shuffle per epoch
    for step, (x, y) in enumerate(loader):
        x, y = x.cuda(local, non_blocking=True), y.cuda(local, non_blocking=True)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            loss = criterion(model(x), y) / ACCUM
        loss.backward()
        if (step + 1) % ACCUM == 0:
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(); opt.zero_grad(set_to_none=True)
    if rank == 0: torch.save({"model": model.module.state_dict(), "opt": opt.state_dict(), "epoch": epoch}, "ckpt.pt")
```
**[+]** For giant models: **FSDP / DeepSpeed ZeRO** (shard params, grads, optimizer state), tensor parallelism (Megatron), checkpoint often and support **resume**, use spot-instance-friendly training.

## 5.7 AutoML **[BOOK]**

- Jeff Dean's TF Dev Summit 2018: replace ML expertise with 100× compute.
- **Soft AutoML: hyperparameter tuning** (LR, batch size, layers, units, dropout, quantization level). Weaker models with tuned hyperparameters beat stronger ones (Melis et al.). **Graduate Student Descent** joke. Methods: **random search**, grid, **Bayesian optimization**; tools: auto-sklearn, Keras Tuner, Ray Tune, Optuna. Tune sensitive hyperparameters most carefully. **Never tune on test split.**
- **Hard AutoML: NAS** = search space + performance estimation strategy + search strategy (RL, evolution; random is prohibitive). Discrete search space; building blocks (convs, linear, activations, pooling, identity, zero).
- **Learned optimizers:** replace update rules with a NN; train once over many tasks (Metz et al.) and reuse; can bootstrap itself. Costly → few can afford; but outputs like **EfficientNet** (10× efficiency) are reusable.

**[CODE] Optuna with pruning**
```python
import optuna
def objective(trial):
    lr = trial.suggest_float("lr", 1e-5, 1e-2, log=True)
    wd = trial.suggest_float("wd", 1e-6, 1e-1, log=True)
    drop = trial.suggest_float("dropout", 0.0, 0.5)
    model = build(drop); opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=wd)
    for epoch in range(20):
        train_one_epoch(model, opt); val = evaluate(model, val_loader)     # VALIDATION split, never test
        trial.report(val, epoch)
        if trial.should_prune(): raise optuna.TrialPruned()
    return val
study = optuna.create_study(direction="maximize", pruner=optuna.pruners.MedianPruner(n_warmup_steps=3))
study.optimize(objective, n_trials=100)
```

## 5.8 Four phases of ML adoption **[BOOK]**
1. **Before ML** — heuristics (top-3 letters e/t/a = 30% next-letter accuracy; Facebook feed chronological until 2011). "You might find non-ML solutions work fine" (Zinkevich).
2. **Simplest ML** — logistic regression, XGBoost, kNN; build the full framework early.
3. **Optimize simple models** — objectives, hyperparams, features, more data, ensembles; learn decay speed.
4. **Complex systems** — when simple hits limits.
Each phase's solution = baseline for the next.

## 5.9 Offline evaluation **[BOOK]**

- Ideally eval methods same in dev and prod, but prod lacks ground truth. Drone-intrusion example: no way to measure missed intrusions.
- **Baselines** (metrics alone mean little — FID 10.3? F1 0.90 on 90% positive class ≈ random!):
  1. **Random** — uniform or label-distribution. For 90/10 labels: uniform → F1 0.167, acc 0.5; label-dist → F1 0.1, acc 0.82.
  2. **Simple heuristic** (reverse chronological feed).
  3. **Zero-rule** (always most common class / most-used app).
  4. **Human** baseline.
  5. **Existing solutions** (if/else logic, third-party). Slightly inferior can still be useful if cheaper/easier.
- Good ≠ useful (replacement of experts must match experts; even better-than-human self-driving may not be trusted; next-word predictor can be worse than native speaker and still useful).

### Evaluation methods beyond accuracy
- **Perturbation tests**: add noise/clip (cough covid app: hospital vs user recordings); pick model best on perturbed data. Noise sensitivity → maintenance burden + adversarial risk.
- **Invariance tests**: change sensitive attributes (race, name, gender) → output must not change (better: exclude them from features; may be legally required).
- **Directional expectation tests**: bigger lot size shouldn't lower price; smaller sqft shouldn't raise it.
- **Model calibration**: predicted 70% should be right 70% of the time. Team A predicted 70%, won 60% → miscalibrated. Nate Silver: calibration is "single most important" test of a forecast. Matters for (1) **calibrated recommendations** (80% romance/20% comedy mix), (2) **click estimation** (ranking doesn't need calibration; forecasting counts does). Measure with **calibration curve** (`sklearn.calibration.calibration_curve`), fix with **Platt scaling** (`CalibratedClassifierCV`), isotonic regression.
- **Confidence measurement**: per-instance usefulness threshold; below → discard, loop in human, or ask user.
- **Slice-based evaluation**: overall metric hides subgroup failure. Model A 98%/80% (90/10 split → 96.2%) vs Model B 95%/95% (95%). Also **critical slices** (paid vs free users). **Simpson's paradox** (kidney stone study: A better in both groups, B better overall: A 273/350=78%, B 289/350=83%; Berkeley 1973 admissions). Slicing methods: **heuristics** (mobile vs desktop, locale), **error analysis** (found hidden button on phones), **slice finder** (beam search/clustering/decision trees → prune → rank).

**[CODE] Behavioral test suite (pytest) + calibration + slices**
```python
# tests/test_model_behavior.py
import numpy as np, pandas as pd, pytest
from sklearn.calibration import calibration_curve, CalibratedClassifierCV
from sklearn.metrics import brier_score_loss, f1_score

@pytest.fixture(scope="module")
def model(): return load_model("models/candidate")

# --- Invariance: sensitive attribute flip must not change score
@pytest.mark.parametrize("col,vals", [("gender", ["M","F"]), ("first_name", ["Emily","Jamal"])])
def test_invariance(model, sample_df, col, vals):
    a = sample_df.assign(**{col: vals[0]}); b = sample_df.assign(**{col: vals[1]})
    diff = np.abs(model.predict_proba(a)[:,1] - model.predict_proba(b)[:,1])
    assert np.percentile(diff, 99) < 0.01

# --- Directional expectations
def test_lot_size_monotonic(model, houses):
    lo = model.predict(houses); hi = model.predict(houses.assign(lot_size=houses.lot_size*1.2))
    assert (hi >= lo - 1e-6).mean() > 0.99

# --- Perturbation robustness
def test_noise_robustness(model, X_test, y_test):
    rng = np.random.default_rng(0); noisy = X_test + rng.normal(0, 0.05, X_test.shape)
    assert f1_score(y_test, model.predict(noisy)) > 0.9 * f1_score(y_test, model.predict(X_test))

# --- Minimum performance vs baseline
def test_beats_baseline(model, X_test, y_test, baseline_f1):
    assert f1_score(y_test, model.predict(X_test)) > baseline_f1 * 1.05

# --- Slice-based gate
def slice_report(df, y_col, pred_col, slice_cols, metric):
    rows = []
    for c in slice_cols:
        for v, g in df.groupby(c):
            rows.append({"slice": f"{c}={v}", "n": len(g), "metric": metric(g[y_col], g[pred_col])})
    return pd.DataFrame(rows).sort_values("metric")

def test_no_slice_regression(df):
    rep = slice_report(df, "y", "pred", ["platform", "country", "is_paid"], f1_score)
    rep = rep[rep.n >= 200]
    assert rep.metric.min() > 0.8 * rep.metric.median()

# --- Calibration
def expected_calibration_error(y, p, bins=10):
    frac, mean_p = calibration_curve(y, p, n_bins=bins, strategy="quantile")
    return np.abs(frac - mean_p).mean()
def test_calibrated(model, X_test, y_test): assert expected_calibration_error(y_test, model.predict_proba(X_test)[:,1]) < 0.05
# Fix: CalibratedClassifierCV(base, method="isotonic" or "sigmoid", cv=5)

# --- Confidence gating in serving
def predict_or_defer(proba, hi=0.9, lo=0.1):
    return "auto_positive" if proba >= hi else "auto_negative" if proba <= lo else "human_review"
```

---

# PART 6 — Chapter 6: Model Deployment

## 6.1 What "deploy" means **[BOOK]**
- Making the model running and accessible; **production is a spectrum** (plots for a business team → millions of users).
- Easy: endpoint + AWS + Streamlit in an hour. **Hard:** millions of users, ms latency, 99% uptime, alerting, root cause, seamless updates.
- Team structures: same team deploys vs hand-off (overhead, slow updates, hard debug).
- **Exporting/serialization** = model definition + parameter values (`tf.keras.Model.save()` → SavedModel; `torch.onnx.export()` → ONNX).
- **Don't think categorically** (batch vs online, edge vs cloud are anchors, not rules).

## 6.2 Four deployment myths **[BOOK]**
1. *You only deploy one or two models* — Uber: thousands; Google: thousands training concurrently; Booking.com 150+; 41% of orgs with >25k employees have >100 models in production. 20 countries × 10 models = 200.
2. *Model performance stays the same* — software rot + **concept drift**; models best right after training.
3. *You won't need to update models often* — ask "How often **can** I update?" Etsy 50/day, Netflix 1000s/day, AWS every 11.7s; Weibo ML update cycle 10 minutes; Alibaba/ByteDance similar.
4. *Most ML engineers don't need scale* — most engineers work at companies ≥100 employees.

## 6.3 Batch vs online prediction **[BOOK]**

| | Batch (asynchronous) | Online (synchronous / on-demand) |
|---|---|---|
| Frequency | Periodic (e.g., every 4h) | As requests arrive |
| Optimized for | Throughput | Latency |
| Features | Batch features only | Batch + streaming features |
| Good for | Recommendations (Netflix), accumulated data | Fraud detection, speech, translation |
| Weakness | Stale to preference changes; must know requests in advance; wasteful for inactive users | Latency constraints |

- Both can process multiple samples; "sync/async" less confusing.
- **Online with batch features only** (Fig 6-5) vs **online with streaming features** (Doordash: restaurant mean prep time [batch] + current orders, available couriers [stream]) = "streaming prediction".
- **Hybrid:** precompute popular queries, online for the tail. DoorDash: batch restaurant recs (too many), online item recs.
- Batch = a **trick to hide inference latency**; only 2% of users log in daily → 98% wasted (GrubHub 31M users, 622k daily orders).
- Online prediction isn't inherently less efficient (batch queries dynamically).
- Catastrophic when batch: HFT, self-driving, voice, phone unlock, fall detection, fraud.
- Requirements to move to online: **(near) real-time pipeline** (streaming) + **fast-inference model**.
- **Unify batch and stream pipelines:** two pipelines = major bug source (feature skew when teams maintain separately). Uber and Weibo unify via **Flink**.

**[CODE] Online serving (FastAPI) with validation, batching, timeouts, metrics**
```python
# serve.py — uvicorn serve:app --workers 4
import asyncio, time, numpy as np, onnxruntime as ort
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from prometheus_client import Histogram, Counter, make_asgi_app

app = FastAPI(); app.mount("/metrics", make_asgi_app())
LAT = Histogram("predict_latency_seconds", "latency", buckets=(.005,.01,.025,.05,.1,.25,.5,1))
ERR = Counter("predict_errors_total", "errors", ["type"]); PRED = Histogram("prediction_value", "scores", buckets=np.linspace(0,1,11).tolist())

sess = ort.InferenceSession("model.onnx", providers=["CPUExecutionProvider"])
MODEL_VERSION = "2026-09-24-abc123"

class Req(BaseModel): features: list[float] = Field(min_length=32, max_length=32)
class Resp(BaseModel): score: float; model_version: str

# Dynamic micro-batching: trade a few ms of wait for big throughput gains
QUEUE: asyncio.Queue = asyncio.Queue(); MAX_BATCH, MAX_WAIT = 32, 0.005
async def batcher():
    while True:
        first = await QUEUE.get(); batch = [first]; t0 = time.perf_counter()
        while len(batch) < MAX_BATCH and (time.perf_counter() - t0) < MAX_WAIT:
            try: batch.append(QUEUE.get_nowait())
            except asyncio.QueueEmpty: await asyncio.sleep(0.0005)
        x = np.array([b[0] for b in batch], dtype=np.float32)
        out = sess.run(None, {"input": x})[0][:, 0]
        for (_, fut), s in zip(batch, out): fut.set_result(float(s))
@app.on_event("startup")
async def _s(): asyncio.create_task(batcher())

@app.post("/predict", response_model=Resp)
async def predict(r: Req):
    with LAT.time():
        fut = asyncio.get_event_loop().create_future(); await QUEUE.put((r.features, fut))
        try: score = await asyncio.wait_for(fut, timeout=0.1)          # hard latency budget
        except asyncio.TimeoutError: ERR.labels("timeout").inc(); raise HTTPException(504, "timeout")
        PRED.observe(score); return Resp(score=score, model_version=MODEL_VERSION)

@app.get("/healthz")
def health(): return {"ok": True, "version": MODEL_VERSION}
```

**[CODE] Batch prediction job (Airflow-style) + hybrid fallback**
```python
import pandas as pd, redis, json, datetime as dt

def batch_predict(ds: str):
    users = pd.read_parquet(f"s3://feat/users/ds={ds}")           # batch features
    users["score"] = model.predict_proba(users[FEATS])[:, 1]
    users["model_version"] = MODEL_VERSION; users["scored_at"] = dt.datetime.utcnow()
    users.to_parquet(f"s3://preds/churn/ds={ds}/part.parquet")   # write partitioned, idempotent
    r = redis.Redis(); pipe = r.pipeline()
    for uid, s in zip(users.user_id, users.score): pipe.setex(f"pred:{uid}", 6*3600, s)   # TTL = staleness bound
    pipe.execute()

def get_prediction(uid, feats):                                   # hybrid: precomputed -> online fallback
    cached = redis_client.get(f"pred:{uid}")
    if cached is not None: return float(cached)
    return float(model.predict_proba([feats])[0, 1])              # cold users / new items
```

## 6.4 Model compression **[BOOK]**

Goal: faster inference / smaller footprint (three levers: make model smaller [compression], make inference faster [optimization], make hardware faster).

| Technique | Idea | Pros | Cons |
|---|---|---|---|
| **Low-rank factorization** | Replace high-dim tensors with lower-dim; compact conv filters (SqueezeNet: AlexNet accuracy with 50× fewer params; MobileNet depthwise-separable: K²C → K²+C → ~8–9× fewer for K=3) | Big speedups | Architecture-specific, needs expertise |
| **Knowledge distillation** | Student mimics teacher (or ensemble); DistilBERT: −40% size, 97% capability, 60% faster | Architecture-agnostic (RF student, transformer teacher) | Needs a good teacher; sensitive to app/architecture |
| **Pruning** | Remove nodes (change arch) or zero low-value weights (sparse; >90% non-zero reduction possible). Debate: Liu et al. (value is architecture; retrain dense) vs Zhu et al. (large sparse beats small dense) | Smaller storage | Sparsity needs HW/runtime support to speed up |
| **Quantization** | Fewer bits per parameter: 32→16 (half), 8-bit fixed-point, 1-bit (BinaryConnect, XNOR-Net; Xnor.ai acquired by Apple ~$200M) | Most general; smaller + faster + bigger batches | Rounding error, range overflow/underflow |

- Quantization: post-training (train FP32 → quantize) vs quantization-aware training (QAT). 100M params × 32 bits = 400MB → 16-bit = 200MB. NVIDIA Tensor Cores (mixed precision), TPU **bfloat16**. TF Lite, PyTorch Mobile, TensorRT support post-training quantization.
- **Roblox case study:** BERT → DistilBERT → dynamic shapes → INT8 quantization on CPUs; 25k inf/s @ <20ms; quantization gave the biggest win (7× latency, 8× throughput) — but *output quality changes weren't reported* → validate!

**[CODE] Compression toolkit**
```python
import torch, torch.nn as nn, torch.nn.functional as F
import torch.nn.utils.prune as prune

# ---- Post-training dynamic quantization (Linear/LSTM; CPU)
qmodel = torch.ao.quantization.quantize_dynamic(model.eval(), {nn.Linear}, dtype=torch.qint8)

# ---- Half precision inference (GPU)
model = model.half().cuda()

# ---- Pruning (unstructured L1) then make permanent
for m in model.modules():
    if isinstance(m, nn.Linear): prune.l1_unstructured(m, "weight", amount=0.5); prune.remove(m, "weight")
sparsity = sum((p == 0).sum().item() for p in model.parameters()) / sum(p.numel() for p in model.parameters())

# ---- Knowledge distillation loss (Hinton)
def distill_loss(student_logits, teacher_logits, y, T=2.0, alpha=0.5):
    soft = F.kl_div(F.log_softmax(student_logits / T, -1), F.softmax(teacher_logits / T, -1), reduction="batchmean") * T * T
    hard = F.cross_entropy(student_logits, y)
    return alpha * soft + (1 - alpha) * hard

teacher.eval()
for x, y in loader:
    with torch.no_grad(): t = teacher(x)
    loss = distill_loss(student(x), t, y); loss.backward(); opt.step(); opt.zero_grad()

# ---- Depthwise-separable conv (MobileNet-style low-rank)
class DWSep(nn.Module):
    def __init__(s, cin, cout, k=3):
        super().__init__(); s.dw = nn.Conv2d(cin, cin, k, padding=k//2, groups=cin); s.pw = nn.Conv2d(cin, cout, 1)
    def forward(s, x): return s.pw(F.relu(s.dw(x)))

# ---- ALWAYS gate compression on quality + latency
def compression_gate(orig, comp, X, y, max_drop=0.01, min_speedup=1.5): ...
```

## 6.5 Cloud vs edge **[BOOK]**

- **Cloud**: easy start, managed. Downsides: **cost** (Pinterest/Intuit hundreds of millions/yr; mistakes bankrupt startups), needs network.
- **Edge** (browsers, phones, watches, cars, cameras, robots, FPGAs, ASICs): works offline/unreliable network; no **network latency** (often bigger than inference latency — ResNet50 30ms→20ms is irrelevant vs seconds of network); better **privacy** (no data transit; GDPR easier; but device theft risk); lower cloud bills.
- Requirements: compute, memory, battery. Full BERT on a phone kills battery.
- Hardware race: Google, Apple, Tesla chips; startups raised billions; >30B edge devices by 2025 (projected).

### Compiling & optimizing for hardware
- Framework must be supported by hardware vendor (PyTorch on TPUs: Sep 2020, 2.5 years after TPU public). Hardware differs: CPU scalar, GPU 1-D vector, TPU 2-D tensor; memory layouts L1/L2/L3.
- **Intermediate Representations (IR)** = middle man between frameworks and hardware: framework → high-level IR (computation graph) → low-level IRs → machine code ("**lowering**", not translation). Tools: **TVM, MLIR, XLA, TensorRT, ONNX**.
- **Model optimization**: naive frameworks composition can be 23× slower than hand-optimized (Stanford DAWN, Palkar). Optimizing compilers automate.
  - **Local:** vectorization, parallelization, **loop tiling** (hardware-dependent access order), **operator fusion** (one loop instead of two).
  - **Global:** vertical/horizontal graph fusion (TensorRT).
  - **ML to optimize ML:** hand-designed heuristics are non-optimal and non-adaptive. cuDNN autotune (`torch.backends.cudnn.benchmark=True`, convs only); **autoTVM** (subgraphs; measures real runtimes to train a cost model; ~70 trials to beat cuDNN on ResNet-50). Slow (hours-days) but **one-time & cacheable**.
- **ML in browsers:** JS (TensorFlow.js, Synaptic, brain.js) is slow; **WebAssembly (WASM)** — open standard, 93% device support (2021), but ~45–55% slower than native (Jangda et al.).

**[CODE] Export → optimize → benchmark**
```python
import torch, onnx, onnxruntime as ort, numpy as np, time
model.eval(); dummy = torch.randn(1, 3, 224, 224)

torch.onnx.export(model, dummy, "model.onnx", input_names=["input"], output_names=["logits"],
                  dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}}, opset_version=17)
onnx.checker.check_model(onnx.load("model.onnx"))

# Post-training INT8 (ONNX Runtime)
from onnxruntime.quantization import quantize_dynamic, QuantType
quantize_dynamic("model.onnx", "model.int8.onnx", weight_type=QuantType.QInt8)

# Parity test: exported == original (put in CI!)
sess = ort.InferenceSession("model.onnx", providers=["CPUExecutionProvider"])
with torch.no_grad(): ref = model(dummy).numpy()
out = sess.run(None, {"input": dummy.numpy()})[0]
np.testing.assert_allclose(ref, out, rtol=1e-3, atol=1e-4)

# Latency benchmark with percentiles (warmup!)
def bench(fn, n=500, warmup=50):
    for _ in range(warmup): fn()
    ts = []
    for _ in range(n): t = time.perf_counter(); fn(); ts.append((time.perf_counter() - t) * 1e3)
    return {f"p{q}": np.percentile(ts, q) for q in (50, 90, 95, 99)} | {"mean": np.mean(ts)}
print(bench(lambda: sess.run(None, {"input": dummy.numpy()})))

# Fusion/compile in one line (PyTorch 2)
compiled = torch.compile(model, mode="max-autotune")
```

## 6.6 [+] Safe release patterns (essential complement to the book)

| Pattern | What | When |
|---|---|---|
| **Shadow deployment** | New model receives live traffic; predictions logged, not served | Validate latency/outputs risk-free |
| **Canary** | 1–5% traffic → ramp | Detect regressions with limited blast radius |
| **A/B test** | Randomized comparison on business metrics | Prove causal lift |
| **Interleaving** | Mix results from both rankers within a list | Faster ranking evaluation |
| **Multi-armed / contextual bandits** | Adaptive traffic allocation | Exploration + exploitation |
| **Blue/green + instant rollback** | Two envs; flip | Zero-downtime updates |

**[CODE] Deterministic traffic split + shadow**
```python
import hashlib, asyncio, logging
def bucket(user_id: str, salt="exp42") -> float:
    return int(hashlib.sha256(f"{salt}:{user_id}".encode()).hexdigest(), 16) % 10_000 / 10_000

CANARY = 0.05
async def route(user_id, feats):
    primary = await stable.predict(feats)
    asyncio.create_task(shadow_log(user_id, feats, primary))            # shadow: never blocks/serves
    if bucket(user_id) < CANARY: return await candidate.predict(feats)   # sticky assignment per user
    return primary

async def shadow_log(uid, feats, primary):
    try: cand = await asyncio.wait_for(candidate.predict(feats), 0.2)
    except Exception as e: return logging.warning("shadow failed %s", e)
    logging.info("shadow uid=%s primary=%.4f cand=%.4f", uid, primary, cand)

# Promotion gate (CI/CD for models)
def promote(cand_metrics, prod_metrics):
    return (cand_metrics["auc"] >= prod_metrics["auc"] - 0.002
            and cand_metrics["p99_ms"] <= 1.1 * prod_metrics["p99_ms"]
            and cand_metrics["worst_slice"] >= 0.9 * prod_metrics["worst_slice"])
```

---

# PART 7 — Chapter 7: Why ML Systems Fail in Production

## 7.1 Natural labels & feedback loops **[BOOK]**

- **Natural ground-truth labels**: prediction evaluable automatically (Google Maps ETA vs actual trip time). Others: create feedback channels (Translate suggestions, Facebook likes).
- **Feedback loop length** = time from prediction served to feedback.
  - **Short** (minutes): recommenders (click = positive), CTR. Some longer: blog posts (hours), Stitch Fix clothes (weeks).
  - **No explicit negatives**: no click after a **window** ⇒ negative. **Window length = speed vs accuracy trade-off**; premature negatives happen (Twitter ads: most clicks within 5 min, some hours later → CTR underestimated).
  - **Long** (weeks–months): fraud (dispute window 1–3 months). Good for quarterly reports, bad for fast issue detection.

## 7.2 What is an ML failure? **[BOOK]**
- Failure = violated expectation. **Operational** (latency, throughput, timeouts, 404, OOM, segfault) — easy to detect. **ML performance** (accuracy ≥99%) — hard; **fail silently**.
- **Software system failures:** dependency failure, deployment failure (old binary, permissions), hardware failure (overheating, cosmic rays), downtime/crash. Google study of 96 pipeline breakages: **60 non-ML** (distributed systems/orchestrator errors, data pipeline joins/wrong structures). Majority of ML engineering is engineering.
- **ML-specific failures:** data collection/processing problems, poor hyperparameters, **training/inference pipeline mismatch**, **data distribution shifts**, **edge cases**, **degenerate feedback loops**.

## 7.3 Production data differs from training data **[BOOK]**
- Assumption: unseen data ~ same stationary distribution. Wrong because:
  1. Training data can't represent real world (finite, biased) → **train-serving skew** (even emoji encoding differences).
  2. **The world isn't stationary** (Wuhan searches pre/post COVID). Shifts: sudden (competitor pricing, new region, celebrity mention), gradual (culture, language), seasonal.
- Many "shifts" on dashboards are actually **internal errors** (pipeline bugs, wrong imputation, feature inconsistency, wrong stats, wrong model version, UI change). One monitoring CTO: ~80% of detected drifts are human error.

## 7.4 Edge cases **[BOOK]**
- Car that's safe 99.9% of the time but catastrophically fails 0.1%—would you use it? Applies to medical, traffic control, eDiscovery, chatbots emitting racist output (brand risk).
- **Outlier** = data property (differs from others). **Edge case** = performance property (model performs much worse). Not all outliers are edge cases (jaywalker on highway handled correctly).
- Outliers can hurt training (skew decision boundary) but at inference you can't drop queries; transform ("mechin learnin"→"machine learning") or build robust models. A sudden rise in edge-case frequency may signal drift.

## 7.5 Degenerate feedback loops **[BOOK]**
- Predictions influence feedback that trains the next model. Songs A vs B: A ranked slightly higher → clicked more → ranked even higher. Names: **exposure bias, popularity bias, filter bubbles, echo chambers**. Resume screening with feature X (Stanford/Google/male) reinforces X.
- **Detect:** offline recommender: measure **popularity diversity** — aggregate diversity, coverage of long-tail items; **hit rate vs popularity buckets** (Chia et al. 2021). In prod: homogeneity growing over time.
- **Correct:**
  1. **Randomization** (TikTok: every video gets an initial random traffic pool of up to hundreds of impressions to gauge unbiased quality) — improves diversity, costs UX; use **contextual bandits** for smarter exploration (Ch.8); Schnabel et al.: small randomization + causal inference.
  2. **Positional features** — encode display position as a feature during training, set to False (or a constant) at inference; better: two models (P(seen/considered | position) × P(click | seen)).

**[CODE] Degenerate-loop mitigation**
```python
import numpy as np, pandas as pd

# --- Detect: popularity-bucketed hit rate & coverage
def popularity_report(recs: pd.DataFrame, interactions: pd.DataFrame, catalog_size: int):
    pop = interactions.item_id.value_counts()
    bucket = pd.cut(pop, [0, 100, 1000, 10_000, np.inf], labels=["<100","100-1k","1k-10k",">10k"])
    hit = recs.assign(hit=recs.item_id.isin(interactions[["user_id","item_id"]].apply(tuple, axis=1)))  # simplify
    coverage = recs.item_id.nunique() / catalog_size
    gini = lambda x: (np.abs(np.subtract.outer(x, x)).mean() / (2 * x.mean()))
    return {"coverage": coverage, "exposure_gini": gini(recs.item_id.value_counts().to_numpy())}

# --- Correct 1: epsilon-greedy exploration (+ log propensities for unbiased offline evaluation)
def recommend(scores, k=5, eps=0.1, rng=np.random.default_rng()):
    n = len(scores); top = np.argsort(-scores)[:k]; out, props = [], []
    for slot in range(k):
        if rng.random() < eps:
            item = rng.integers(n); p = eps / n + (1-eps) * (item == top[slot])
        else:
            item = top[slot]; p = (1-eps) + eps / n
        out.append(item); props.append(p)
    return out, props                                        # LOG props => inverse-propensity-weighted training/eval

# --- Correct 2: positional feature
train["is_first_pos"] = (train.position == 1).astype(int)     # or position number
model.fit(train[FEATS + ["is_first_pos"]], train.clicked)
serve_X = candidates[FEATS].assign(is_first_pos=0)            # neutralize at inference, then rank
ranking = np.argsort(-model.predict_proba(serve_X)[:, 1])

# --- IPS-weighted training to de-bias position/exposure
w = 1.0 / np.clip(train.propensity, 0.05, 1.0)
model.fit(train[FEATS], train.clicked, sample_weight=w)
```

## 7.6 Data distribution shifts **[BOOK]**

- **Source distribution** = training; **target distribution** = production. Studied since 1986.

### Notation
Joint P(X,Y) = P(Y|X)P(X) = P(X|Y)P(Y).

| Shift | What changes | What stays | Example |
|---|---|---|---|
| **Covariate shift** | P(X) | P(Y\|X) | More women >40 in training than in prod; marketing attracts wealthier users; cough recordings clinic vs phone |
| **Label shift (prior shift)** | P(Y) | P(X\|Y) | Preventive drug lowers cancer rate for everyone (age distribution among the sick same) |
| **Concept drift (posterior shift)** | P(Y\|X) | P(X) | Same 3-bed SF apartment: $2M → $1.5M at COVID start; seasonal price patterns |
| Feature change | Feature set/values/units | — | age years→months; pipeline bug → NaNs |
| Label schema change | Set of Y values; both P(Y), P(X\|Y) | — | Credit score 300–850 → 250–900; NEGATIVE → SAD + ANGRY (retrain softmax layer) |

- Covariate shift often coexists with label shift; not all covariate shifts imply label shift (subtle). Case P(X|Y) changes while P(Y) constant is unstudied.
- Causes of covariate shift: **sample selection bias**, artificial alteration (resampling for imbalance), **active learning** by-product, real environment/application changes.
- **Importance weighting**: estimate density ratio P_target(x)/P_train(x), weight training data, train. Needs knowing target distribution.
- Multiple shifts can occur simultaneously.

### Detecting shifts
- If ground truth is available soon: monitor accuracy metrics (accuracy, F1, recall, AUC-ROC). Otherwise monitor **input distribution P(X)** (most common), predictions, and where possible labels (Black Box Shift Estimation, Lipton).
- **Summary statistics** (mean, median, variance, quantiles, skew, kurtosis): TensorFlow Data Validation does this. Necessary, not sufficient.
- **Two-sample tests**: Kolmogorov–Smirnov (nonparametric, **1-D only**, expensive, false positives), Least-Squares Density Difference, **MMD** (kernel; multivariate; mostly research), Learned Kernel MMD. **alibi-detect** library. **Reduce dimensionality first.** Heuristic: detectable from a small sample ⇒ serious; requires huge sample ⇒ probably ignorable (statistical significance ≠ practical importance).
- **Time-scale windows:** window choice determines what you detect (weekly cycles invisible in <7-day windows; Fig. 7-3). Spatial vs temporal shifts. **Sliding vs cumulative statistics** (cumulative hides dips: hours 16–18). Short windows = faster detection but more false alarms. Platforms offer **merge** (hourly → daily) and root-cause-analysis features. Time-series decomposition (Lyft).

### Addressing shifts
1. **Train on massive datasets** (research default).
2. **Adapt without target labels** (Zhang 2013; Zhao 2020 domain-invariant representation) — underexplored.
3. **Retrain with labeled target data** (industry standard): from scratch on old+new vs **fine-tune** on new; choose data window (since drift began? since last fine-tune? last N days?) experimentally; fine-tuning is cheaper but often insufficient.
- Relation: domain adaptation / transfer learning.
- **Design for robustness**: trade-off between **feature performance and stability** (app rank → bucket; category stable but weaker). Separate models per market to update at different rates (SF vs rural Arizona).
- Not all degradation is ML; find **human errors** first.

**[CODE] Drift detection toolkit (PSI, KS, chi², MMD-lite, domain classifier, windows)**
```python
import numpy as np, pandas as pd
from scipy import stats
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import cross_val_predict
from sklearn.metrics import roc_auc_score

# ---------- Numeric: PSI (Population Stability Index) ----------
def psi(expected, actual, bins=10, eps=1e-6):
    qs = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    qs[0], qs[-1] = -np.inf, np.inf
    e = np.histogram(expected, qs)[0] / len(expected) + eps
    a = np.histogram(actual, qs)[0] / len(actual) + eps
    return float(np.sum((a - e) * np.log(a / e)))          # <0.1 stable, 0.1–0.25 moderate, >0.25 major

# ---------- Numeric: KS two-sample ----------
def ks_drift(ref, cur, alpha=0.01):
    stat, p = stats.ks_2samp(ref, cur); return {"ks": stat, "p": p, "drift": p < alpha and stat > 0.1}   # effect-size guard

# ---------- Categorical: chi-square / new-category rate ----------
def cat_drift(ref: pd.Series, cur: pd.Series, alpha=0.01):
    cats = ref.value_counts().index.union(cur.value_counts().index)
    r, c = ref.value_counts().reindex(cats, fill_value=0), cur.value_counts().reindex(cats, fill_value=0)
    chi, p, *_ = stats.chi2_contingency(np.vstack([r, c]))
    unseen = (~cur.isin(ref.unique())).mean()
    return {"p": p, "drift": p < alpha, "unseen_rate": unseen}

# ---------- Multivariate: classifier two-sample test (domain classifier) ----------
def domain_classifier_drift(ref: pd.DataFrame, cur: pd.DataFrame):
    X = pd.concat([ref, cur]); y = np.r_[np.zeros(len(ref)), np.ones(len(cur))]
    p = cross_val_predict(GradientBoostingClassifier(), X, y, cv=5, method="predict_proba")[:, 1]
    return roc_auc_score(y, p)      # ~0.5 = same distribution; >0.7 = strong drift; feature importances => which features

# ---------- Importance weighting (covariate shift correction) ----------
def importance_weights(X_train, X_target, clip=(0.05, 20)):
    X = np.vstack([X_train, X_target]); y = np.r_[np.zeros(len(X_train)), np.ones(len(X_target))]
    clf = GradientBoostingClassifier().fit(X, y); p = clf.predict_proba(X_train)[:, 1]
    w = (p / (1 - p)) * (len(X_train) / len(X_target))          # density ratio p_t(x)/p_s(x)
    return np.clip(w, *clip)

# ---------- Windows: sliding (not cumulative) + seasonality-aware reference ----------
def rolling_drift(df, col, ts="ts", ref_days=28, win="1D", thr=0.2):
    out = []
    for day, g in df.set_index(ts).groupby(pd.Grouper(freq=win)):
        if g.empty: continue
        ref = df[(df[ts] < day) & (df[ts] >= day - pd.Timedelta(days=ref_days))][col]
        same_dow = ref[pd.to_datetime(df.loc[ref.index, ts]).dt.dayofweek == day.dayofweek]   # weekly seasonality
        base = same_dow if len(same_dow) > 100 else ref
        out.append((day, psi(base, g[col])))
    return pd.DataFrame(out, columns=["day", "psi"]).assign(alert=lambda d: d.psi > thr)

# ---------- Sliding vs cumulative accuracy ----------
df["correct"] = (df.pred == df.label)
sliding = df.set_index("ts").correct.resample("1H").mean()
cumulative = df.set_index("ts").correct.expanding().mean()      # hides dips!

# ---------- Data validation: catch internal errors that look like drift ----------
def validate_batch(df, ref_stats):
    issues = []
    for c, s in ref_stats.items():
        nulls = df[c].isna().mean()
        if nulls > s["null_rate"] + 0.05: issues.append(f"{c}: null rate {nulls:.2%} vs {s['null_rate']:.2%}")
        if df[c].dtype.kind in "fi" and not (s["min"] <= df[c].min() and df[c].max() <= s["max"] * 1.5):
            issues.append(f"{c}: out-of-range")
    return issues
```

**[CODE] Continuous retraining trigger (fine-tune vs scratch)**
```python
def should_retrain(metrics_today, ref, psi_by_feature, label_lag_days):
    reasons = []
    if any(v > 0.25 for v in psi_by_feature.values()): reasons.append("feature drift")
    if metrics_today.get("ap") is not None and metrics_today["ap"] < 0.95 * ref["ap"]: reasons.append("perf drop")
    if metrics_today["pred_mean_shift"] > 0.1: reasons.append("prediction drift")   # proxy when labels are late
    return reasons

def retrain(strategy="finetune"):
    data = load_window(days=14) if strategy == "finetune" else load_window(days=180)
    cand = finetune(prod_model, data) if strategy == "finetune" else train_from_scratch(data)
    report = evaluate_on_recent_holdout(cand, prod_model)   # evaluate on NEWEST data, time-split
    return cand if promote(report["cand"], report["prod"]) else prod_model
```

---

# PART 8 — Beyond the Book: Modern Best Practices **[+]**

## 8.1 Reference architecture

```
        ┌─────────────┐   ┌────────────────┐   ┌───────────────┐
Sources →│ Ingestion   │→ │ Lake/Warehouse │→ │ Feature Store │──┐
        │ (Kafka/CDC) │   │ (Parquet/Iceb.)│   │ offline+online│  │
        └─────────────┘   └────────────────┘   └───────────────┘  │
                                   ↓                              ↓
                        ┌──────────────────┐   ┌─────────────┐  ┌───────────────┐
                        │ Training pipeline │→ │ Registry &  │→ │ Serving (online│
                        │ (orchestrated)    │   │ Eval gates  │  │ /batch/edge)   │
                        └──────────────────┘   └─────────────┘  └───────┬───────┘
                                   ↑                                    ↓
                        ┌──────────────────┐   ┌────────────────────────────────┐
                        │ Labels/feedback   │←─│ Monitoring: data, model, system │
                        └──────────────────┘   └────────────────────────────────┘
```

## 8.2 Testing pyramid for ML
1. **Data tests** (schema, ranges, null rates, uniqueness, freshness) — pandera / Great Expectations / Soda.
2. **Unit tests** for feature functions, loss functions, post-processing.
3. **Training tests**: overfit-one-batch, loss decreases, deterministic with seed, no NaN.
4. **Model behavior tests**: invariance, directional, slices, calibration, baseline gate.
5. **Integration tests**: end-to-end on tiny data; **training/serving parity test**.
6. **Performance tests**: p50/p99 latency, memory, throughput under load (Locust/k6).
7. **Online checks**: shadow, canary, A/B.

**[CODE] Training–serving parity test**
```python
def test_train_serve_parity(sample_raw_events):
    offline = batch_feature_pipeline(sample_raw_events)                 # used for training
    online = pd.DataFrame([online_feature_service(e) for e in sample_raw_events.to_dict("records")])
    pd.testing.assert_frame_equal(offline[FEATS].reset_index(drop=True), online[FEATS], rtol=1e-5, check_dtype=False)
```

## 8.3 Monitoring stack (what to watch)

| Layer | Metrics | Alert on |
|---|---|---|
| **System** | p50/p95/p99 latency, QPS, error rate, CPU/GPU/mem, queue depth | SLO breach |
| **Data quality** | null rate, schema violations, freshness, volume, new categories | any change vs baseline |
| **Data drift** | PSI/KS/chi² per feature; multivariate domain classifier AUC | sustained drift |
| **Prediction drift** | score distribution, class balance, confidence histogram | shift |
| **Model quality** | accuracy/AP on delayed labels, sliding windows, per-slice | drop / slice gap |
| **Business** | CTR, conversion, revenue, complaints | vs control |
| **Fairness** | metric parity across groups | gap |

**[CODE] Prediction logging for later joins (essential for labels + retraining)**
```python
import uuid, json, time
def log_prediction(features: dict, score: float, model_version: str, position=None, propensity=None):
    rec = {"prediction_id": str(uuid.uuid4()), "ts": time.time(), "model_version": model_version,
           "features": features, "score": score, "position": position, "propensity": propensity}
    kafka_producer.produce("predictions", json.dumps(rec).encode())   # later JOIN with labels on prediction_id
    return rec["prediction_id"]

# Delayed-label join with maturity window
def build_labeled(preds, labels, window_days):
    j = preds.merge(labels, on="prediction_id", how="left")
    mature = j.ts < (pd.Timestamp.utcnow() - pd.Timedelta(days=window_days)).timestamp()
    return j[mature].assign(label=lambda d: d.label.fillna(0))       # no dispute/click in window => negative
```

## 8.4 CI/CD/CT (continuous training)
- **CI**: lint, unit, data tests, model tests on PR.
- **CD**: build container, register model, promote via gates, canary, auto-rollback.
- **CT**: scheduled or drift-triggered retraining (Airflow/Dagster/Kubeflow/Prefect), producing a new registered candidate that goes through the same gates.
- Everything **versioned**: code (git), data (snapshot id), features (definition hash), model (registry), config, environment (Docker digest).

**[CODE] Orchestrated pipeline skeleton (Prefect-style)**
```python
from prefect import flow, task

@task(retries=2) def extract(ds): ...
@task def validate(df): assert not validate_batch(df, REF_STATS), "data quality failed"; return df
@task def build_features(df): return feature_pipeline.transform(df)
@task def train(X, y, params): return fit(X, y, params)
@task def evaluate(model): return run_gates(model)         # behavior tests, slices, baseline, latency
@task def register(model, report):
    if report["passed"]: registry.register(model, stage="Staging")

@flow
def training_flow(ds, params):
    df = validate(extract(ds)); X, y = build_features(df)
    m = train(X, y, params); r = evaluate(m); register(m, r)
```

## 8.5 Cost & efficiency
- Use cheapest sufficient model; distill; quantize; cache; batch; autoscale to zero when idle; spot instances for training; right-size GPUs; monitor $/1k predictions as an SLO.
- **Caching predictions** keyed by input hash for repeated queries; semantic cache for LLMs.

## 8.6 Security & privacy
- Data poisoning defenses (provenance, outlier filtering), adversarial robustness testing, model extraction rate limits, PII minimization/anonymization, differential privacy/federated learning where needed, GDPR right-to-delete → lineage to remove a user's data and retrain; secrets management; access controls on model artifacts.

## 8.7 Responsible ML checklist
- Document intended use (**model card**) and data (**datasheet**).
- Exclude protected attributes *and proxies* where required; test invariance; slice metrics by group; monitor post-deployment; human-in-the-loop for high-stakes; provide explanations (SHAP) and recourse; audit logs.

## 8.8 LLM-era additions (foundation-model systems)
The book's principles still hold; new components:
| Concern | Pattern |
|---|---|
| **Adaptation** | Prompting → RAG → fine-tuning (LoRA/QLoRA) in that order of cost |
| **Retrieval** | Chunk → embed → vector index (pgvector/FAISS/…) → rerank; version the index like data |
| **Evaluation** | Golden sets, LLM-as-judge (calibrate vs humans), rubric tests, regression suites per prompt version |
| **Inference optimization** | KV cache, continuous batching, quantization (INT8/INT4), speculative decoding (vLLM/TGI/TensorRT-LLM) |
| **Guardrails** | Input/output filters, schema-constrained decoding, PII redaction, prompt-injection defenses |
| **Monitoring** | Token cost, latency (TTFT + tokens/s), refusal/hallucination rates, user feedback signals, drift in prompts/topics |
| **Feedback loops** | Thumbs up/down → preference data → DPO/RLHF; beware degenerate loops (sycophancy) |

**[CODE] Minimal RAG eval harness**
```python
def eval_rag(golden, retrieve, generate, judge):
    rows = []
    for ex in golden:                                   # {question, gold_answer, gold_doc_ids}
        docs = retrieve(ex["question"], k=5)
        hit = any(d.id in ex["gold_doc_ids"] for d in docs)                 # retrieval recall@5
        ans = generate(ex["question"], docs)
        rows.append({"hit": hit, "faithful": judge.faithful(ans, docs), "correct": judge.correct(ans, ex["gold_answer"])})
    return pd.DataFrame(rows).mean()
```

## 8.9 Bandits for exploration (Ch. 8 preview)
```python
import numpy as np
class ThompsonBernoulli:
    def __init__(self, n): self.a, self.b = np.ones(n), np.ones(n)
    def select(self): return int(np.argmax(np.random.beta(self.a, self.b)))
    def update(self, arm, reward): self.a[arm] += reward; self.b[arm] += 1 - reward
```
Use for new-item cold start, model selection between candidates, and to break popularity loops.

---

# APPENDIX

## A. Formula sheet
- Latency→throughput (serial): qps = 1/latency. Batched: qps = batch_size / batch_time.
- Min-max: x' = (x−min)/(max−min). Standardize: (x−μ)/σ.
- Precision = TP/(TP+FP); Recall = TP/(TP+FN); F1 = 2PR/(P+R).
- Cross-entropy: −Σ pᵢ log qᵢ. Class weight: Wᵢ = N/nᵢ. Focal: −(1−p_t)^γ log p_t.
- Mixup: x = γx₁+(1−γ)x₂. Importance weight: P(x)/Q(x). Reservoir keep prob: k/n.
- Ensemble (3 uncorrelated, accuracy p): p³ + 3p²(1−p) (p=0.7 → 0.784).
- Depthwise-separable params: K²+C vs K²C (per filter position, book's simplification).
- Quantization memory: params × bits/8 (100M × 32 → 400MB).
- Joint decomposition: P(X,Y)=P(Y|X)P(X)=P(X|Y)P(Y). Covariate: P(X)↑ ; Label: P(Y)↑ ; Concept: P(Y|X)↑.
- Hash space: 2^b buckets (b=18 → 262,144).
- PSI = Σ (aᵢ−eᵢ) ln(aᵢ/eᵢ).

## B. Decision tables

**Which sampling?** rare classes matter → stratified · stream of unknown length → reservoir · known domain importance → weighted · expensive target distribution → importance sampling · quick prototype → convenience (accept bias).

**Missing values?** MCAR & <0.1% rows → delete rows · MNAR → impute + missing indicator · MAR → impute conditional on observed variable · >50% missing → keep only if missingness itself is predictive.

**Which shift?** inputs differ, relationship same → covariate · label mix differs, class-conditional inputs same → label · same inputs, different outcomes → concept · features/labels redefined → feature/schema change · always check **pipeline bugs first**.

**Online vs batch?** unpredictable inputs / need freshness / fraud → online · huge catalogue, tolerant to staleness → batch · mixed → hybrid.

**Cloud vs edge?** privacy, offline, network latency, cloud cost → edge · big models, fast iteration, elastic scale → cloud.

**Compression?** fastest broad win → quantization · have big teacher → distillation · CNN mobile design → low-rank/depthwise · storage → pruning (needs sparse runtime).

## C. Master checklist (print this)

**Before building:** non-ML baseline? ML framing (input/output/loss)? must-have constraints? feedback loop length? cost of errors?
**Data:** sampling bias reviewed? lineage tracked? label guidelines + agreement? class imbalance strategy? duplicates removed? time/group split?
**Features:** train-only statistics? missing indicators? unseen-category handling? one definition for train+serve? coverage/overlap checks? leakage scan?
**Model:** simple baseline first? fair experiment budgets? seeded & tracked? overfit-one-batch? metrics beyond accuracy (PR-AUC, per-class)? calibrated? slices? behavioral tests?
**Deploy:** export parity test? latency percentiles vs SLO? batching/timeouts? shadow → canary → rollout, instant rollback? compression validated for quality?
**Operate:** prediction logging with IDs? data-quality + drift + performance + business monitors? delayed-label pipeline? retraining trigger + gates? on-call + runbooks? feedback-loop mitigation (exploration, position features)? fairness monitoring?

## D. Glossary (quick)
**ACID/BASE** transaction guarantees · **Artifact** file produced by experiment · **Batch/Streaming features** static/dynamic · **Concept drift** P(Y|X) changes · **Covariate shift** P(X) changes · **Data lineage** provenance tracking · **Degenerate feedback loop** outputs shape future inputs · **Edge case** input with catastrophic model failure · **ELT/ETL** load-then-transform / transform-then-load · **Feature crossing** combining features · **Focal loss** loss emphasizing hard examples · **Hashing trick** hash categories into fixed buckets · **IR** intermediate representation · **Label multiplicity** conflicting labels · **Label shift** P(Y) changes · **Lowering** compiling high-level to hardware code · **Mixup** blending samples/labels · **NAS** neural architecture search · **OLTP/OLAP** transactional/analytical processing · **Pipeline parallelism** micro-batch model parallelism · **Platt scaling** logistic calibration · **Reservoir sampling** uniform sampling on streams · **SSGD/ASGD** synchronous/asynchronous SGD · **Train-serving skew** train ≠ serve distribution or code · **Weak supervision** labels from heuristics.

## E. Errata & caveats noticed in the source text
- p90 for the 10-request latency example depends on percentile method; treat with real volumes.
- Quota-sampling age groups in source overlap ("30–60" and "above 50"); define non-overlapping bins.
- Hard-AutoML/other chapters reference Ch.8 (monitoring, continual learning, contextual bandits) which is not in this excerpt — the bandit and monitoring code above is provided as a complement.
- Several book statistics (company counts, prices, dates) are as of 2021; re-verify before citing.

---
*End of notes. Suggested study order: Part 0 → Ch.3–4 (data/features) → Ch.5 (modeling/eval) → Ch.6–7 (deploy/failure) → Part 8 + checklists.*
