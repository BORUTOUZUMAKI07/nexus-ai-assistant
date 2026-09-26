# AI Engineering — Complete Reference Notes
### System Design Patterns for LLMs, RAG, and Agents
*(Consolidated theory + production code patterns)*

---

# PART 1 — LLMs

## 1.1 What is an LLM?
- An LLM predicts the **next token** given prior tokens — the same act as finishing "Once upon a…" → "time".
- Trained on massive text: books, articles, code, conversations.
- **Token** = word, sub-word, or punctuation unit. Text → tokens → model predicts next token → repeat = generation.
- **Formal definition**: a Transformer-based neural network trained on massive corpora to predict the next token, which through this process acquires understanding, generation, and reasoning ability.

## 1.2 Why LLMs Were Needed
- Pre-LLM AI: one model per task (translation model, summarizer, sentiment classifier) → fragmented, didn't scale.
- LLMs learn the **general structure of language**, so one model performs many tasks without task-specific engineering.
- Language encodes reasoning, facts, explanations — a large enough model internalizes these and generalizes across tasks.

## 1.3 What Makes an LLM "Large"
Scale = three axes:
1. **Parameters** — internal trainable values (weights).
2. **Training data volume**.
3. **Compute** used to train.

- Small models mimic style but fail at reasoning/abstraction/generalization.
- **Emergent behavior**: as scale ↑, models spontaneously gain instruction-following, multi-step reasoning, zero-shot generalization — not hand-coded, it emerges from capacity.

## 1.4 How LLMs Are Built (Architecture Components)

| Component | Role |
|---|---|
| **Transformer** | Looks at all tokens simultaneously; finds which parts of text are relevant to each other (self-attention) |
| **Tokenization** | Breaks text into sub-word tokens → numeric IDs; keeps vocab manageable, handles any input |
| **Transformer layers (stacked)** | Each layer refines token representations via attention + refinement |
| **Positional encoding** | Injects order information since attention itself is order-agnostic |
| **Parameters** | Billions of learned weights storing patterns |
| **Distributed training** | Model/data/compute sharded across many GPUs (data parallel, tensor parallel, pipeline parallel) |

## 1.5 How to Train an LLM From Scratch — 4 Stages

```
0) Random init → gibberish output
1) Pre-training           → next-token prediction on huge corpora → learns grammar/facts, but only "continues" text
2) Instruction Fine-Tuning (IFT) → trained on instruction-response pairs → becomes conversational
3) Preference Fine-Tuning (PFT / RLHF) → reward model trained on human A/B preference → PPO updates LLM
4) Reasoning Fine-Tuning (RFT / RLVR) → verifiable correctness reward (math/logic) → GRPO-style updates
```

- **Stage 1 — Pre-training**: absorbs grammar/world knowledge; model just continues text (no chat behavior).
- **Stage 2 — IFT**: trains on (instruction, response) pairs → learns to follow prompts, format replies, answer Qs, summarize, code.
- **Stage 3 — PFT/RLHF**: humans pick preferred response of two → reward model learns to predict preference → LLM updated via **PPO**. Aligns model where no single "correct" answer exists.
- **Stage 4 — Reasoning FT**: for math/logic there IS a correct answer → use **correctness as reward** directly (Reinforcement Learning with **Verifiable** Rewards, RLVR). **GRPO** (DeepSeek) is the popular algorithm.

**Production takeaway**: pick the fine-tuning stage/technique based on whether you have labeled data and whether the task is verifiable (see SFT vs RFT decision tree, §3.9).

## 1.6 How LLMs Work — Conditional Probability & Decoding

- Next-token prediction = conditional probability: `P(next_word | previous_words)`.
- Model learns a high-dimensional probability distribution over sequences; **parameters = the learned distribution**.
- Training is **supervised** (predict the actual next token in the corpus).
- **Problem**: always picking argmax → repetitive, low-creativity output.
- **Solution — Temperature**: reshapes softmax before sampling.
  - Low temp (~0) → concentrated distribution → near-greedy, deterministic.
  - High temp (0.7–1.0+) → flatter distribution → random/creative, more noise.

```python
# Temperature scaling pseudo-code
import numpy as np
def apply_temperature(logits, temperature):
    scaled = np.array(logits) / max(temperature, 1e-6)
    exp = np.exp(scaled - np.max(scaled))
    return exp / exp.sum()
```

## 1.7 The 7 LLM Generation Parameters (+ bonus)

1. **max_tokens** — hard cap on output length. Too low → truncation; too high → wasted compute.
2. **temperature** — randomness control (low=deterministic/QA, high=creative/brainstorm).
3. **top_k** — restrict sampling pool to k most probable tokens (e.g., k=5). Too small → repetitive.
4. **top_p (nucleus sampling)** — sample from smallest token set whose cumulative probability ≥ p (e.g., 0.9). More adaptive than top_k.
5. **frequency_penalty** — penalizes tokens already used often; positive discourages repetition (good for summarization), negative encourages it (good for poetry/refrains).
6. **presence_penalty** — rewards introducing *new* tokens not yet seen; higher = more novelty/exploration.
7. **stop_sequences** — custom strings that immediately halt generation; critical for structured output (e.g. stop at `}` for JSON).
- **Bonus — min-p sampling**: dynamically adjusts the sampling pool as a fraction of the top token's probability (e.g., keep tokens ≥10% as likely as the top one). Tightens when confident, loosens when uncertain — self-adaptive vs. static top-p.

```python
response = client.chat.completions.create(
    model="gpt-4o",
    messages=messages,
    max_tokens=800,
    temperature=0.3,
    top_p=0.9,
    frequency_penalty=0.4,
    presence_penalty=0.0,
    stop=["\n\n", "```"]
)
```

## 1.8 4 LLM Text Generation (Decoding) Strategies (+ bonus SLED)

1. **Greedy** — always pick highest-probability token. Simple but repetitive/degenerate loops.
2. **Multinomial sampling** — sample proportional to probability distribution; temperature controls randomness.
3. **Beam search** — keeps top-k *partial sequences* ("beams") alive at each step, approximating global sequence-probability maximization (not just next-token). Used where correctness > creativity (e.g., machine translation).
4. **Contrastive search** — balances fluency + diversity by penalizing candidate tokens too similar to already-generated text; avoids "stuck in a loop" issues in long generations (stories).
- **Bonus — SLED (Self-Logits Evolution Decoding)**: instead of using only final-layer logits, aggregates logits across *all* layers, nudging final logits toward cross-layer consensus. No retraining/extra data needed; improves factual grounding.

## 1.9 3 Techniques to Train an LLM Using Another LLM (Distillation)

Real examples: Llama 4 Scout/Maverick trained from Llama 4 Behemoth; Gemma 2/3 from Gemini; DeepSeek-R1 distilled into Qwen/Llama 3.1.

Distillation happens at **pre-training** (train teacher+student together, e.g. Llama 4) and/or **post-training** (train teacher first, then distill, e.g. DeepSeek) stages.

1. **Soft-label distillation** — teacher's full softmax probability distribution over vocab is the training target for student. Maximum knowledge transfer, but requires teacher weights AND massive storage (e.g., 100k vocab × 5T tokens × fp8 ≈ 500M GB).
2. **Hard-label distillation** — only the teacher's final one-hot output token is used as label (student still produces its own softmax). Far cheaper; used by DeepSeek→Qwen/Llama distillation.
3. **Co-distillation** — both teacher and student start untrained; both generate softmax on the same batch; teacher trains on ground truth (hard labels), student trains to match teacher's evolving soft labels (+ hard labels, since early soft labels are unreliable). Used in Llama 4 Behemoth→Scout/Maverick.

## 1.10 4 Ways to Run LLMs Locally

| Tool | Notes |
|---|---|
| **Ollama** | `ollama pull <model>` then `ollama run <model>`; Python package + LlamaIndex/CrewAI integrations |
| **LMStudio** | Desktop app, local-only data, free for personal use, chat-like UI, load/eject models |
| **vLLM** | Fast local serving with OpenAI-compatible API, few lines of code |
| **LlamaCPP** | Minimal setup, good performance, C++ backend |

```bash
# Ollama quick start
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1
ollama run llama3.1
```

## 1.11 Transformer vs. Mixture of Experts (MoE)

- MoE keeps total parameter count large but **activates only a subset of "experts" per token** → scales capacity without proportional compute cost.
- **Architectural diff**: Transformer decoder block uses one feed-forward network (FFN); MoE decoder block replaces it with multiple smaller FFN "experts."
- **Router**: a learned softmax classifier over experts; picks **top-K** experts per token per layer. Different tokens can route to different experts; different layers can pick different experts for the same token.
- **Challenge 1 — expert collapse**: early in training, one expert gets picked, improves, gets picked again → rich-get-richer, other experts stay undertrained.
  - **Fix**: (a) add noise to router logits so other experts can surface; (b) hard top-K mask (set non-top-K logits to −∞ before softmax) so only K experts get gradient signal, spreading training over time.
- **Challenge 2 — token imbalance**: some experts see far more tokens.
  - **Fix**: cap tokens-per-expert; overflow tokens route to next-best expert.
- **Net effect**: more total parameters, but only a fraction active per token → faster inference. Example: Mixtral 8x7B (MoE).

---

# PART 2 — Prompt Engineering

## 2.1 What Is Prompt Engineering
- The "steering wheel" for an LLM — doesn't change weights, changes **instructions**, which changes output.
- Good prompts help the model: think step-by-step, follow constraints, stay focused, avoid shallow answers.
- Fastest, lowest-effort lever for better results.

## 2.2 3 Prompting Techniques for Reasoning (+ bonus ARQ)

1. **Chain of Thought (CoT)** — nudge the model to reason step by step before the final answer (e.g., "Let's think step by step"). Improves accuracy on multi-step problems.
2. **Self-Consistency** — generate multiple independent CoT reasoning paths (via temperature/sampling), take the **majority-vote final answer**. More robust on ambiguous/complex tasks, but doesn't evaluate *how* reasoning was done — only the final answer's consistency.
3. **Tree of Thoughts (ToT)** — at each reasoning step, explore multiple candidate next-steps as branches of a tree; a separate evaluator scores which branch is most promising (like a search algorithm over reasoning paths). More compute-intensive but outperforms plain CoT on hard problems.

- **Bonus — Attentive Reasoning Queries (ARQ)**: solves the "free-form thinking drifts / forgets rules mid-conversation" failure mode of CoT/ToT. Instead of open-ended "thinking aloud," the LLM is forced to fill a **structured JSON schema of targeted domain-specific queries** at each reasoning step (e.g., "does this violate policy X?", "which tool applies?"). This (a) **reinstates critical instructions** mid-conversation, (b) makes reasoning **auditable/verifiable**. Benchmarked (87 scenarios): ARQ 90.2% vs CoT 86.1% vs direct 81.5%. Implemented in **Parlant** (open-source instruction-following agent framework) across guideline-proposer, tool-caller, and message-generator modules.

```python
# ARQ-style structured reasoning query (conceptual)
arq_schema = {
    "applicable_guidelines": "list the policies relevant to this turn",
    "tool_needed": "yes/no + which tool",
    "constraint_check": "does the draft response violate any stated rule?",
    "final_response": "the customer-facing message"
}
# Prompt the LLM to fill this JSON before producing the reply — every field is a forced, auditable check.
```

## 2.3 Verbalized Sampling (VS)

- **Problem — mode collapse**: RLHF alignment causes the model to collapse toward a narrow set of "safe," predictable, high-typicality responses, losing creative diversity that existed in the pre-trained base model.
- **Root cause — typicality bias**: human annotators (during preference labeling) systematically prefer familiar/predictable answers even when a novel answer is equally good — so the reward model over-boosts already-likely outputs, sharpening the distribution.
- **Fix (training-free)**: instead of prompting for *one instance* ("Tell me a joke"), prompt for a **distribution** ("Generate 5 responses with their probabilities. Tell me a joke."). This forces the aligned model to surface the rich diversity still present in its pre-trained weights.
- **Results**: 1.6–2.1x diversity gain over direct prompting, quality maintained/improved; larger models (GPT-4.1, Gemini-2.5-Pro) benefit more (up to 2x); recovers ~66.8% of base-model diversity (vs much lower for direct prompting) across SFT/DPO/RLVR stages; **stacks** with temperature/top-p.

```python
prompt = """Generate 5 different responses to the user's request, each with an estimated probability.
Return as JSON: [{"response": "...", "probability": 0.0-1.0}, ...]

User request: Tell me a joke about programmers."""
```

## 2.4 JSON Prompting

- Natural language is vague → LLM guesses format/detail level → inconsistent outputs.
- **JSON prompting** forces field/value thinking → eliminates gray areas, since LLMs are heavily trained on structured API/web data ("their native language").
- Benefits:
  1. **Structure = certainty** — no ambiguity about what's expected.
  2. **You control outputs** — consistent structure every run → easy programmatic consumption.
  3. **Reusable templates** — shareable across teams, plug directly into APIs/DBs, no manual reformatting.
- Alternatives exist: **Claude excels at XML**; **Markdown** gives structure with less overhead. The real lesson: **structure > syntax** — pick whichever structured format best matches the target model.

```python
json_prompt = """
Extract the following fields from the email below and return ONLY valid JSON:
{
  "sender_intent": "string",
  "key_dates": ["YYYY-MM-DD", ...],
  "action_items": ["string", ...],
  "urgency": "low|medium|high"
}

Email:
<<<{email_text}>>>
"""
```

---

# PART 3 — Fine-Tuning

## 3.1 What Is Fine-Tuning
- Adjusting a **pre-trained model's weights** on a new dataset so it better matches a new distribution/task, without training from scratch.
- Classic example: BERT (92k+ citations) fine-tuned + augmented layers for downstream tasks.

## 3.2 Issues With Traditional Fine-Tuning at LLM Scale
- GPT-3 (175B params) = 350GB just for weights (fp16).
- If 1,000 users each traditionally fine-tune → 350,000 GB to store.
- Problems: storage explosion, billing/usage ambiguity (fine-tuned-but-unused models), must decide whether to always keep 350GB in memory for instant serving.
- **Traditional (full) fine-tuning is infeasible for LLMs** at any real user scale.

## 3.3 5 LLM Fine-Tuning (PEFT) Techniques (+ 3 bonus)

1. **LoRA** — freeze W; add two small low-rank matrices A (d×r) and B (r×k) alongside it; train only A, B. `ΔW ≈ A·B`.
2. **LoRA-FA** ("Frozen-A") — freeze matrix A too; train only B. Saves activation memory.
3. **VeRA** — A and B are **frozen, random, shared across all layers**; only small per-layer **scaling vectors b, d** are trained. Extremely parameter-efficient.
4. **Delta-LoRA** — also updates the frozen W itself, using the *delta of (A·B) between two consecutive training steps* added into W.
5. **LoRA+** — same A/B structure as LoRA, but uses a **higher learning rate for B** than for A → more optimal convergence (empirically found).

**Bonus:**
- **LoRA-drop** — adds LoRA to every layer, trains briefly, measures each layer's LoRA activation strength; drops LoRA from layers where activation ≈ 0 (minimal influence) → cheaper, faster fine-tuning, little accuracy loss.
- **QLoRA** — combine LoRA with **quantization** of the frozen base weights (fp32 → 4/8-bit) → drastically less memory (~75% reduction going fp32→int8) at a small precision/quality trade-off.
- **DoRA (Weight-Decomposed LoRA)** — decomposes frozen W into **magnitude (m)** and **direction (V)** components, fine-tunes them independently → better parameter efficiency/performance than vanilla LoRA.

### LoRA — Mathematical / Implementation Detail
- Full fine-tuning: `W_new = W + ΔW`, and ΔW has the same (huge) dimensions as W → memory-infeasible.
- LoRA insight: keep W **frozen**, decompose `ΔW = A · B` where `A` is `d×r`, `B` is `r×k`, and `r << d,k` (low rank) — so `A·B` is small to store/train but reconstructs a `d×k` update.
- Design space: 2D grid of (parameters trained) × (rank/expressiveness). Full fine-tuning = upper-right corner. Empirically, an efficient sweet spot is bottom-left (few trainable params, modest rank) — you don't need to fine-tune everything.

```python
import torch
import torch.nn as nn

class LoRAWeights(nn.Module):
    """From-scratch LoRA layer: ΔW = A @ B, scaled by alpha/r."""
    def __init__(self, d, k, r=8, alpha=16):
        super().__init__()
        self.A = nn.Parameter(torch.randn(d, r) * 0.01)  # Gaussian init
        self.B = nn.Parameter(torch.zeros(r, k))          # zero init -> ΔW=0 at start
        self.scale = alpha / r

    def forward(self, x):
        return (x @ self.A @ self.B) * self.scale

class MyNeuralNetworkWithLoRA(nn.Module):
    def __init__(self, base_model, dims, r=8, alpha=16):
        super().__init__()
        self.model = base_model
        for p in self.model.parameters():
            p.requires_grad = False  # freeze base model
        self.loralayer1 = LoRAWeights(*dims["fc1"], r, alpha)
        self.loralayer2 = LoRAWeights(*dims["fc2"], r, alpha)
        self.loralayer3 = LoRAWeights(*dims["fc3"], r, alpha)

    def forward(self, x):
        x = torch.relu(self.model.fc1(x) + self.loralayer1(x))
        x = torch.relu(self.model.fc2(x) + self.loralayer2(x))
        x = torch.relu(self.model.fc3(x) + self.loralayer3(x))
        return self.model.fc4(x)  # fc4 left frozen / un-adapted
```

**Production pattern — HuggingFace PEFT (real-world equivalent):**
```python
from peft import LoraConfig, get_peft_model, TaskType
from transformers import AutoModelForCausalLM

model = AutoModelForCausalLM.from_pretrained("meta-llama/Llama-3.1-8B")
lora_config = LoraConfig(
    r=16, lora_alpha=32, lora_dropout=0.05,
    target_modules=["q_proj", "v_proj"],   # commonly just attention weights (per original LoRA paper)
    task_type=TaskType.CAUSAL_LM,
)
model = get_peft_model(model, lora_config)
model.print_trainable_parameters()  # verify tiny % of params are trainable
```

## 3.4 Generating an LLM Fine-Tuning Dataset (Instruction Fine-Tuning, IFT)

- A pre-trained (not instruction-tuned) LLM just **continues** text — it isn't conversational yet.
- Synthetic IFT data generation pipeline (e.g., using **Distilabel**, open-source):
  1. Input an instruction (from a seed dataset).
  2. **Two LLMs** generate candidate responses.
  3. A **judge LLM** rates both responses.
  4. Best response is paired with the instruction → synthetic (instruction, response) row.
- This produces a dataset for supervised fine-tuning.

```python
from distilabel.pipeline import Pipeline
from distilabel.steps.tasks import TextGeneration, UltraFeedback
from distilabel.llms import OllamaLLM

with Pipeline(name="ift-synth") as pipeline:
    gen_a = TextGeneration(llm=OllamaLLM(model="llama3"))
    gen_b = TextGeneration(llm=OllamaLLM(model="llama3:70b"))
    judge = UltraFeedback(llm=OllamaLLM(model="llama3:70b"))
    # combine gen_a + gen_b outputs -> judge -> best response kept
    gen_a >> judge
    gen_b >> judge

distiset = pipeline.run(dataset=seed_dataset)
```

## 3.5 SFT vs RFT

| | **SFT (Supervised Fine-Tuning)** | **RFT (Reinforcement Fine-Tuning)** |
|---|---|---|
| Data | Static labeled (prompt, completion) pairs | No static labels — online reward signal |
| Process | Adjust weights to match given completions | Model explores outputs; reward function scores correctness; GRPO-style updates over time |
| Failure mode | Tends to memorize | Learns from rewards, explores new strategies |

### Decision tree: which fine-tuning method to use?
```
Do you have labelled (ground-truth) data?
├── No → Is the task verifiable (checkable automatically)?
│         ├── No  → use RLHF (need human preference signal)
│         └── Yes → use RFT (correctness auto-checkable)
└── Yes → How much labelled data do you have?
          ├── Large dataset      → use SFT
          └── Tiny dataset       → Does reasoning (CoT) help this task?
                                    ├── Yes → RFT
                                    └── No  → SFT
```

## 3.6 Build a Reasoning LLM Using GRPO (Hands-On Pattern)

**GRPO (Group Relative Policy Optimization)**: RL fine-tuning method for math/reasoning using **deterministic reward functions** — no labeled data required.

Pipeline:
```
dataset + reasoning system prompt ("Think step by step…")
   → LLM generates MULTIPLE candidate responses (sampling)
   → each response scored by reward function(s)
   → GRPO loss computed from group-relative rewards → backprop → policy improves
```

**5-step implementation pattern (Unsloth + HF TRL + Qwen3-4B-Base):**
```python
# 1) Load base model
from unsloth import FastLanguageModel
model, tokenizer = FastLanguageModel.from_pretrained("unsloth/Qwen3-4B-Base", max_seq_length=2048)

# 2) LoRA config (PEFT, avoid full fine-tune)
model = FastLanguageModel.get_peft_model(
    model, r=16, target_modules=["q_proj","k_proj","v_proj","o_proj"],
    lora_alpha=16, use_gradient_checkpointing="unsloth",
)

# 3) Dataset — reasoning-formatted (system prompt enforcing structure + Q + expected-format answer)
from datasets import load_dataset
dataset = load_dataset("open-r1/OpenR1-Math-220k", split="train")
def format_example(ex):
    return {"prompt": [{"role":"system","content": REASONING_SYSTEM_PROMPT},
                        {"role":"user","content": ex["problem"]}],
            "answer": ex["answer"]}
dataset = dataset.map(format_example)

# 4) Reward functions — deterministic, no labels needed
def match_format_exactly(completions, **kw): ...
def match_format_approximately(completions, **kw): ...
def check_answer(completions, answer, **kw): ...
def check_numbers(completions, answer, **kw): ...

# 5) GRPO training
from trl import GRPOConfig, GRPOTrainer
training_args = GRPOConfig(
    output_dir="qwen3-reasoner", learning_rate=5e-6,
    num_generations=8, max_completion_length=512,
)
trainer = GRPOTrainer(
    model=model, args=training_args,
    reward_funcs=[match_format_exactly, match_format_approximately, check_answer, check_numbers],
    train_dataset=dataset,
)
trainer.train()
```
Result: base model → measurably stronger reasoning accuracy without any human-labeled reasoning traces.

## 3.7 Bottleneck in Reinforcement Learning: The Environment Problem
- Hardest part of RL isn't the agent — it's the **environment**: rules, actions, reward structure.
- No standard API → every project reinvents environments → not reusable, hard to transfer agents across tasks → massive engineering overhead maintaining/re-implementing environments instead of improving algorithms.

## 3.8 The Solution: PyTorch OpenEnv Framework
- Gymnasium-inspired but **containerized, service-based**.
- Every environment exposes 3 methods: `reset()` (new episode), `step(action)` (apply action, get feedback), `state()` (current state).
- Runs in isolated **Docker containers**, communicates over **HTTP** → reproducible, shareable, consistent across machines.
- Workflow: agent → OpenEnv client → FastAPI app in Docker → env updates state, returns obs/reward/done → agent updates policy, loop continues.
- Example use: fine-tuning GPT-OSS-20B with Unsloth to play 2048 via OpenEnv.

## 3.9 Agent Reinforcement Trainer (ART) — by OpenPipe
- For training **agentic LLMs** (multi-step reasoning, tool calls, plans — not simple discrete actions).
- Handles the hard engineering: running the agent to produce full trajectories, capturing decisions/tool-use/reasoning, scoring trajectories with a custom reward function, updating the model via RL.
- Lightweight client wraps your **existing agent code** with minimal changes; talks to an ART training server (manages rollouts, reward computation, batching, optimization).
- Supports **GRPO** — learns from **trajectory-level** rewards (not token-level labels) — critical for planning/correction/tool-use behaviors.
- Loop: your agent runs → produces trajectory → reward function scores it → GRPO updates policy → repeat, gradually improving agent behavior.

---

# PART 4 — RAG (Retrieval-Augmented Generation)

## 4.1 What Is RAG and Why
Two prior levers (prompt engineering, fine-tuning) are both limited: **the model can only use knowledge it already contains**. LLMs don't know info after training cutoff, private/company data, or anything not in training data. Repeated retraining is impractical/expensive.

- **Retrieval** — fetch info from a knowledge source (DB, vector store).
- **Augmented** — enrich the generation process with that retrieved info.
- **Generation** — produce the final text, grounded in retrieved context.
- Benefit: reduces hallucination, keeps model "real-time" without retraining.

## 4.2 Vector Databases
- Store **unstructured data** (text/image/audio/video) as numeric **vector embeddings** capturing semantic meaning (similar items cluster together — e.g., fruits cluster, cities cluster).
- Enable operations hard for traditional DBs: **similarity search, clustering, classification** over unstructured data.
- Example: e-commerce "similar items" recommendations run on vector DBs behind the scenes.

## 4.3 Purpose of Vector DBs in RAG
- LLM is deployed frozen after a training cutoff; retraining daily is impractical (weeks to train).
- Putting new info directly in the prompt hits **context window limits**.
- **Solution**: embed external knowledge into a vector DB once; at query time, embed the user query with the **same embedding model**, run **approximate nearest neighbor (ANN)** search, retrieve top-k matching chunks (+ their stored raw text/metadata "payload"), and inject them into the prompt alongside the user's question.

## 4.4 RAG Workflow — 8 Steps (End-to-End)

```
[Indexing phase — done once / incrementally]
1. Chunk the document(s)
2. Generate embeddings per chunk (bi-encoder / context embedding model)
3. Store embeddings (+metadata+raw text) in vector DB

[Query phase — done per request]
4. User inputs a query
5. Embed the query (same embedding model as step 2)
6. Retrieve top-k similar chunks (ANN search)
7. (Optional) Re-rank retrieved chunks with a cross-encoder for higher precision
8. Feed re-ranked/retrieved chunks + query into LLM via a prompt template → generate grounded response
```

```python
# Minimal production RAG pipeline pattern
from openai import OpenAI
import chromadb

client = OpenAI()
chroma = chromadb.PersistentClient(path="./vectordb")
collection = chroma.get_or_create_collection("docs")

def embed(text: str) -> list[float]:
    return client.embeddings.create(model="text-embedding-3-large", input=text).data[0].embedding

# --- Indexing ---
for chunk_id, chunk_text in enumerate(chunks):
    collection.add(ids=[str(chunk_id)], embeddings=[embed(chunk_text)],
                    documents=[chunk_text], metadatas=[{"source": "doc1.pdf"}])

# --- Query ---
def rag_query(user_query: str, k: int = 5) -> str:
    q_emb = embed(user_query)
    results = collection.query(query_embeddings=[q_emb], n_results=k)
    context = "\n\n".join(results["documents"][0])
    prompt = f"""Answer using ONLY the context below. Cite sources.

Context:
{context}

Question: {user_query}"""
    resp = client.chat.completions.create(model="gpt-4o", messages=[{"role":"user","content":prompt}])
    return resp.choices[0].message.content
```

## 4.5 5 Chunking Strategies

1. **Fixed-size chunking** — split by fixed char/word/token count, with **overlap** between consecutive chunks to reduce broken context. Simple, easy batch processing; but breaks sentences/ideas mid-way.
2. **Semantic chunking** — split by meaningful units (sentence/paragraph); compute embeddings; merge consecutive segments into one chunk while cosine similarity stays high; start new chunk when similarity drops sharply. Preserves natural flow/complete ideas → improves retrieval accuracy. Downside: relies on a similarity-drop threshold that varies by document.
3. **Recursive chunking** — first split on structural separators (paragraphs/sections); then recursively split any chunk exceeding a size limit further. Preserves flow like semantic chunking; more implementation/compute overhead.
4. **Document-structure-based chunking** — use inherent structure (headings/sections/paragraphs) as chunk boundaries. Assumes clear document structure exists; chunk sizes vary (may exceed model limits) — often combined with recursive splitting as a fallback.
5. **LLM-based chunking** — prompt an LLM to produce semantically isolated, meaningful chunks. Highest semantic accuracy (LLM understands context/meaning beyond heuristics), but most computationally expensive; also constrained by the LLM's own context window.

**Guidance**: semantic chunking performs well broadly, but the right choice depends on content type, embedding model, compute budget — test empirically.

```python
# Fixed-size chunking with overlap
def fixed_size_chunks(text, chunk_size=500, overlap=50):
    chunks = []
    step = chunk_size - overlap
    for i in range(0, len(text), step):
        chunks.append(text[i:i+chunk_size])
    return chunks

# Semantic chunking (cosine-similarity break)
import numpy as np
def cosine(a, b): return np.dot(a,b)/(np.linalg.norm(a)*np.linalg.norm(b))

def semantic_chunks(sentences, embed_fn, threshold=0.75):
    chunks, current = [], [sentences[0]]
    prev_emb = embed_fn(sentences[0])
    for sent in sentences[1:]:
        emb = embed_fn(sent)
        if cosine(prev_emb, emb) >= threshold:
            current.append(sent)
        else:
            chunks.append(" ".join(current))
            current = [sent]
        prev_emb = emb
    chunks.append(" ".join(current))
    return chunks
```

## 4.6 Prompting vs. RAG vs. Fine-Tuning — Decision Framework

Two axes decide the approach:
- **External knowledge needed?**
- **Behavioral adaptation needed?** (tone, vocabulary, structure, style — not facts)

| Knowledge needed | Adaptation needed | Approach |
|---|---|---|
| No | No | **Prompt engineering** |
| Yes | No | **RAG** |
| No | Yes | **Fine-tuning** |
| Yes | Yes | **Hybrid (RAG + Fine-tuning)** |

Example: summarizing internal meeting transcripts full of company jargon needs *both* — RAG for facts, fine-tuning for internal vocabulary/style.

## 4.7 8 RAG Architectures

1. **Naive RAG** — pure vector-similarity retrieval + generation. Best for simple, fact-based queries.
2. **Multimodal RAG** — embeds/retrieves across text, image, audio, etc.; answers combine modalities.
3. **HyDE** — generate a *hypothetical* answer document first, embed that, retrieve using its embedding (since questions aren't semantically similar to answers). See §4.9.
4. **Corrective RAG** — validates retrieved docs against trusted sources (e.g. live web search); filters/corrects context before passing to LLM → keeps info current/accurate.
5. **Graph RAG** — converts retrieved content into a **knowledge graph** capturing entities/relationships; gives LLM structured context alongside raw text → stronger reasoning.
6. **Hybrid RAG** — combines dense vector retrieval + graph-based retrieval in one pipeline; useful when both unstructured text and structured relational data matter.
7. **Adaptive RAG** — dynamically decides simple direct retrieval vs multi-step reasoning chain; complex queries get broken into sub-queries.
8. **Agentic RAG** — AI agents (planning, ReAct, CoT, memory) orchestrate retrieval across multiple sources/tools; best for complex, multi-source, multi-step workflows. See §4.10.

## 4.8 RAG vs Agentic RAG

**Traditional RAG problems**: retrieve-once/generate-once (can't dynamically fetch more if context insufficient); no reasoning through complex/multi-hop queries; zero adaptability (LLM can't change strategy).

**Agentic RAG workflow** (one possible blueprint):
```
1-2. User query → rewriting agent (fix spelling, simplify for embedding)
3.   Decision agent: "do I need more detail?"
4.   If NO  → send rewritten query directly to LLM
5-8. If YES → routing agent picks best source(s) from {vector DB, tools/APIs, internet};
     retrieve relevant context → send to LLM as prompt
9.   Either path produces a draft response
10.  Verifier agent checks: is the answer relevant to query+context?
11.  If YES → return response
12.  If NO  → loop back to step 1, iterate (bounded number of times), else admit failure
```
This makes RAG robust: agentic behavior at each stage keeps sub-outcomes aligned with the overall goal. Architecture is a design choice, not a fixed recipe.

## 4.9 Traditional RAG vs HyDE

- **Core problem**: questions are NOT semantically similar to their answers → irrelevant context gets retrieved (higher cosine similarity to the wrong docs than to the docs containing the actual answer).
- **HyDE (Hypothetical Document Embeddings) pipeline**:
  1. LLM generates a **hypothetical answer H** to query Q (doesn't need to be fully correct).
  2. Embed H using a **contriever** model (often bi-encoder, trained via contrastive learning) → embedding E.
  3. Use E to query the vector DB → retrieve real context C.
  4. Pass **H + C + Q** to the LLM → final answer.
- Why it works despite hallucinated H: the contriever model acts as a near-lossless *compressor* that filters hallucinated details, producing an embedding closer to real documents than the raw question would be.
- Trade-off: better retrieval accuracy, but higher latency and more LLM calls (extra generation step).

```python
def hyde_retrieve(query, llm, embed_fn, vector_db, k=5):
    hypothetical_doc = llm.generate(f"Write a short passage that would answer: {query}")
    h_embedding = embed_fn(hypothetical_doc)
    retrieved = vector_db.similarity_search_by_vector(h_embedding, k=k)
    final_prompt = f"Hypothetical answer:\n{hypothetical_doc}\n\nRetrieved context:\n{retrieved}\n\nQuestion: {query}"
    return llm.generate(final_prompt)
```

## 4.10 Full Fine-Tuning vs. LoRA vs. RAG — Side by Side

| | Full FT | LoRA | RAG |
|---|---|---|---|
| Updates | All weights | Small low-rank adapters only | No weight updates |
| Cost | Very high (size + compute + storage per user) | Low (few trainable params) | No training cost; retrieval infra cost instead |
| Best for | Deep behavioral change, resource-rich orgs | Efficient adaptation/behavior change | Injecting fresh/external knowledge |
| Limitation | Infeasible at LLM scale for many users | Still a training pipeline; behavior-only | Similarity-mismatch (query≠answer); poor for full-corpus summarization (only top-k chunks reach the prompt) |

- RAG's 7-step version (dump data → embed once → embed query → ANN search → augment prompt → generate) reiterates the R-A-G acronym directly.

## 4.11 RAG vs REFRAG (Meta AI)

- **Problem**: most retrieved RAG chunks are irrelevant → LLM processes excess tokens → costs more compute/latency/context budget.
- **REFRAG idea**: compress and filter context at the **vector level** before expansion:
  1. **Chunk compression** — each chunk → single compressed embedding (not hundreds of token embeddings).
  2. **Relevance policy** (lightweight, RL-trained) — evaluates compressed embeddings, keeps only most relevant chunks.
  3. **Selective expansion** — only RL-selected chunks get expanded back to full token embeddings and sent to the LLM; rejected chunks stay as a single compressed vector.
- Full pipeline: encode docs → store → encode query → find relevant chunks + compute token-level embeddings for query and matches → RL policy selects chunks to keep → concatenate (query tokens + selected chunk tokens + compressed-rejected-chunk vector) → send to LLM.
- **Reported gains**: 30.85x faster time-to-first-token (3.75x better than prior SOTA); 16x larger effective context windows; outperforms LLaMA on 16 RAG benchmarks using 2–4x fewer decoder tokens; no accuracy loss across RAG/summarization/multi-turn tasks.

## 4.12 RAG vs CAG (Cache-Augmented Generation)

- **Problem**: RAG re-fetches the same context from the vector DB on every query, even for stable/rarely-changing info → expensive, redundant, slow.
- **CAG**: caches stable info directly in the model's **KV memory** so it doesn't need to be re-embedded/re-retrieved/re-processed every call.
- **RAG + CAG hybrid** (recommended pattern): split knowledge into two layers:
  - **Cold/static data** (company policies, reference guides) → cached once in KV memory.
  - **Hot/dynamic data** (recent interactions, live docs) → still fetched via normal retrieval.
- Key discipline: **be selective about what you cache** — only stable, high-value knowledge; caching everything hits context limits.
- Many APIs (OpenAI, Anthropic) already support **prompt caching** — usable today.

```python
# Anthropic prompt caching pattern (conceptual) — static system content cached, dynamic query not
response = client.messages.create(
    model="claude-sonnet-4-6",
    system=[{"type": "text", "text": STATIC_COMPANY_POLICY_DOC,
             "cache_control": {"type": "ephemeral"}}],
    messages=[{"role": "user", "content": dynamic_user_query}],
)
```

## 4.13 RAG → Agentic RAG → AI Memory: The Evolution

| Era | Behavior | Limitation |
|---|---|---|
| **RAG (2020–2023)** | Retrieve once, generate once; no decision-making | Often retrieves irrelevant context |
| **Agentic RAG** | Agent decides IF retrieval needed, WHICH source, validates IF results useful | Still read-only — can't learn from interactions |
| **AI Memory** | Reads AND writes to external knowledge; learns from past conversations; remembers preferences/context | Enables true personalization + continual learning |

- Mental model: RAG = read-only, one-shot. Agentic RAG = read-only via tool calls. Agent Memory = **read-write** via tool calls.
- Memory is the bridge between static, frozen models and adaptive systems that improve over time **without retraining**.

---

# PART 5 — Context Engineering

## 5.1 What Is Context Engineering
- The shift from "clever prompting" to **systematic orchestration of context**: right information + right tools + right format, delivered dynamically.
- Most agent/LLM-app failures are **not** model quality failures — they're **missing/wrong context** failures.
- RAG is ~80% retrieval, ~20% generation: good retrieval can rescue a weak LLM; bad retrieval defeats even the best LLM.
- **4 components of a context engineering system**:
  1. **Dynamic information flow** — pull context from users, prior turns, external data, tool calls, intelligently combined.
  2. **Smart tool access** — give the right tools, format outputs to be maximally digestible.
  3. **Memory management** — short-term (summarize long conversations) + long-term (remember preferences across sessions).
  4. **Format optimization** — e.g., a short descriptive error message beats a giant JSON blob.
- As models get better, **context quality becomes the bottleneck**, not model capability.

## 5.2 Context Engineering for Agents — 6 Types of Context

Karpathy's framing: *LLM = CPU, context window = RAM* — you're programming the RAM.

1. **Instructions** — who the agent is, why it's acting, how it should behave (steps/tone/format/constraints).
2. **Examples** — behavioral demos, structured examples, anti-patterns; models learn patterns better than rules.
3. **Knowledge** — domain knowledge (business processes, APIs, data models, workflows) bridging prediction → decision-making.
4. **Memory** — continuity across sessions: short-term (reasoning steps, chat history) + long-term (facts, preferences).
5. **Tools** — extends the agent beyond language into real-world action; each tool needs clear params/inputs/examples.
6. **Tool Results** — feeding results back into the model for self-correction/adaptation/dynamic decisions.

### 4 Fundamental Stages of Context Engineering
1. **Writing context** — save context outside the active context window (long-term memory, short-term memory, or a state object) for later use.
2. **Reading (selecting) context** — pull relevant context into the window from a tool, memory, or knowledge base.
3. **Compressing context** — keep only the tokens needed; summarize to remove duplicate/redundant info from multi-turn tool calls.
4. **Isolating context** — split context across multiple sub-agents (each with its own context), sandboxed execution environments, or state objects.

## 5.3 6 Types of Contexts for AI Agents (expanded framing)

1. **Instructions** — who/why/how.
2. **Examples** — good/bad demonstrations.
3. **Knowledge** — domain facts/APIs/workflows.
4. **Memory** — short-term (reasoning steps/chat) + long-term (facts/preferences).
5. **Tools** — extend real-world action, params/inputs/examples defined.
6. **Tool Results** — feed back for self-correction and dynamic decisions.

**Core insight**: "A poor LLM can possibly work with an appropriate context, but a SOTA LLM can never make up for an incomplete context." Production LLM apps need the **full ecosystem** of context, not a single prompt line.

## 5.4 Building a Context Engineering Workflow (Multi-Source Research Assistant Pattern)

Sources aggregated: Documents, Memory, Web search, Arxiv.

```
User query
  → fetch context from: RAG(docs) + Memory(Zep) + Web(Firecrawl) + Arxiv API
  → context-evaluation agent filters aggregated context (drops irrelevant)
  → synthesizer agent generates the final response using filtered context
  → save final response to memory
```

**Reference tech stack** (swap for your own equivalents):
- **Tensorlake** — RAG-ready markdown chunk extraction from complex docs.
- **Milvus** — self-hosted vector DB for indexing/retrieval.
- **Zep** — memory layer using **temporal knowledge graphs**.
- **Firecrawl** — fast web search/scraping, LLM-ready output.
- **arXiv API** — research paper search.
- **CrewAI** — multi-agent orchestration.

```python
# Conceptual CrewAI flow skeleton
from crewai import Agent, Task, Crew

retrieval_agent = Agent(role="Retriever", goal="Gather context from docs, web, arxiv, memory", tools=[rag_tool, web_tool, arxiv_tool, memory_tool])
filter_agent = Agent(role="Context Evaluator", goal="Filter irrelevant context")
synth_agent = Agent(role="Synthesizer", goal="Generate final cited response")

crew = Crew(agents=[retrieval_agent, filter_agent, synth_agent],
            tasks=[gather_task, filter_task, synth_task])
result = crew.kickoff(inputs={"query": user_query})
# persist result to memory (Zep) for future turns
```

## 5.5 Context Engineering in Claude Skills

- **Skills** = reusable, persistent agent abilities without permanently bloating the context window.
- Problem solved: LLMs "forget" unless everything is restated every time — Skills package instructions/examples/edge-cases into small self-contained units loaded **only when relevant**.
- **3-layer context management**:
  - **Layer 1 — Main Context**: always loaded (project config).
  - **Layer 2 — Skill Metadata**: YAML frontmatter only (~2–3 lines, <200 tokens) — Claude scans this to decide relevance.
  - **Layer 3 — Active Skill Context**: full SKILL.md + docs, loaded only when the skill activates.
  - Supporting files (scripts/templates) aren't preloaded at all — fetched on demand, **zero token cost** until used.
- This lets an agent use **hundreds of skills** without hitting context limits.

**Anatomy of a Skill**: a folder containing
- `skill.md` with YAML front matter (tiny relevance descriptor) + Skill Body (detailed instructions/workflows/examples).
- Optional supporting files (scripts/templates/reference docs) fetched only on demand.

**How Skills fit the architecture**: Projects organize workspace; MCP connects tools; Subagents handle delegated reasoning; **Skills package reusable procedural expertise** that all of the above can call on.

**Build-your-own-skill steps**:
1. Identify a repeated workflow.
2. Create a skill folder + `skill.md`.
3. Write YAML front matter + full markdown instructions.
4. Add supporting scripts/examples/resources.
5. Zip and upload to Claude's capabilities (Claude Desktop even has a "Skill Creator" skill to scaffold this).

## 5.6 Manual RAG Pipeline vs Agentic Context Engineering (Multi-Source Reality)

- Naive approach ("embed everything, store in vector DB, do RAG") works for **static single sources** — but real workflows span Gmail, Drive, Slack, Calendar, Linear, etc.
- Example query: *"What's blocking the Chicago office project, and when's our next meeting about it?"* — requires Linear (blockers) + Calendar (meetings) + Gmail (emails) + Slack (discussion). **No naive RAG setup can handle this.**
- Real solution = a full **Agentic Context Retrieval System**, three layers:

**Ingestion layer**
- Connect to apps without auth headaches.
- Process different source types differently before embedding (email ≠ code ≠ calendar).
- Detect source updates and refresh embeddings incrementally (not full re-embed).

**Retrieval layer**
- Expand vague queries to infer true intent.
- Route queries to the correct data source(s).
- Layer multiple search strategies: semantic + keyword + graph-based.
- Enforce **authorization** — only retrieve what the user is allowed to see.
- Weigh recency vs. relevance (recent data usually matters more, but old context still counts).

**Generation layer**
- Produce a **citation-backed** response.

- This is genuinely "months of engineering" — which is why platforms like Vertex AI Search, M365, Amazon Q Business, and open-source **Airweave** (30+ app/DB connectors) exist to solve it.
- Update-detection nuance: naive **timestamp comparison** doesn't tell you if content actually changed (permissions might've changed instead) → wasteful re-embedding. Better: **source-specific hashing** (entity-level hashing, file-content hashing, cursor-based syncing).
- **Core insight**: *context retrieval for agents is an infrastructure problem, not an embedding problem* — design for continuous sync, intelligent chunking, and hybrid search from day one.

---

# PART 6 — AI Agents

## 6.1 What Is an AI Agent
- Without agents: a human is the decision-maker at every iterative step (ask → review → refine → repeat).
- With agents: specialized sub-agents (research/filter/summarize/format) run the **entire pipeline end-to-end**, self-refining without human intervention at each step.
- **Formal definition**: autonomous systems that reason, think, plan, identify relevant sources, extract info, take actions, and self-correct when something goes wrong.

## 6.2 Agent vs LLM vs RAG
- **LLM** = the brain — reasons/generates/summarizes but only from what it already knows (static, no live access).
- **RAG** = feeding the brain fresh information (retrieval + generation, no autonomy over *what* to do).
- **Agent** = the decision-maker — uses the brain + tools, decides what steps to take (call a tool? search? summarize? store?), orchestrates like a real assistant.

## 6.3 Building Blocks of AI Agents — 6 Principles

1. **Role-playing** — a specific role ("Senior contract lawyer") sharpens reasoning/retrieval vs. a generic assistant. Specificity of role → sharper, more relevant output.
2. **Focus/Tasks** — too many tasks/too much data → confusion, inconsistency, hallucination. Prefer **multiple narrowly-focused agents** over one do-everything agent.
3. **Tools** — more tools ≠ better. Give only what's needed (e.g., web search + summarizer + citation manager for a research agent); unnecessary tools (speech-to-text, code exec) confuse and slow the agent.
4. **Cooperation** — multi-agent systems work best when agents **exchange feedback**, not just split tasks blindly (e.g., data-gathering agent → risk-assessment agent → strategy agent → report-writing agent).
5. **Guardrails** — prevent hallucination/looping/bad calls via: limiting tool usage, validation checkpoints before advancing, fallback mechanisms (another agent or human reviewer intervenes on failure).
6. **Memory** — short-term (execution-only recall), long-term (persists across interactions), entity memory (tracks key subjects, e.g. CRM customer details).

### Custom Tools in CrewAI — Production Pattern
```python
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
import requests, os

class CurrencyInput(BaseModel):
    amount: float = Field(...)
    from_currency: str = Field(...)
    to_currency: str = Field(...)

class CurrencyConverterTool(BaseTool):
    name: str = "currency_converter"
    description: str = "Converts an amount between two currencies using live exchange rates."
    args_schema = CurrencyInput

    def _run(self, amount, from_currency, to_currency):
        api_key = os.environ["EXCHANGE_RATE_API_KEY"]
        url = f"https://v6.exchangerate-api.com/v6/{api_key}/pair/{from_currency}/{to_currency}"
        resp = requests.get(url).json()
        if resp.get("result") != "success":
            return f"Error: invalid currency code or API failure."
        rate = resp["conversion_rate"]
        return f"{amount} {from_currency} = {amount*rate:.2f} {to_currency} (rate: {rate})"

from crewai import Agent, Task, Crew
currency_analyst = Agent(
    role="Currency Analyst", goal="Provide accurate real-time currency conversions with insight",
    tools=[CurrencyConverterTool()], backstory="Expert in FX markets."
)
task = Task(description="Convert 5000 USD to EUR and comment on the rate trend.", agent=currency_analyst, expected_output="Conversion + brief insight")
crew = Crew(agents=[currency_analyst], tasks=[task])
print(crew.kickoff())
```

### Custom Tools via MCP (reusable across agents/flows)
```python
# server.py — expose a tool as an MCP server
from mcp.server.fastmcp import FastMCP
import os, requests

mcp = FastMCP("currency-tools")

@mcp.tool()
def convert_currency(amount: float, from_currency: str, to_currency: str) -> str:
    """Convert an amount between currencies using live exchange rates."""
    api_key = os.environ["EXCHANGE_RATE_API_KEY"]
    url = f"https://v6.exchangerate-api.com/v6/{api_key}/pair/{from_currency}/{to_currency}"
    rate = requests.get(url).json()["conversion_rate"]
    return f"{amount} {from_currency} = {amount*rate:.2f} {to_currency}"

if __name__ == "__main__":
    mcp.run(transport="sse", port=8081)  # exposes at http://localhost:8081/sse
```
```python
# Consuming the MCP tool from a CrewAI agent
from crewai import Agent, Task, Crew
from crewai_tools import MCPServerAdapter

server_params = {"url": "http://localhost:8081/sse", "transport": "sse"}
with MCPServerAdapter(server_params) as mcp_tools:
    agent = Agent(role="FX Agent", goal="Convert currency accurately", tools=mcp_tools)
    task = Task(description="Convert 1000 GBP to JPY", agent=agent, expected_output="Converted amount")
    crew = Crew(agents=[agent], tasks=[task])
    print(crew.kickoff())
```

## 6.4 Memory Types in AI Agents

Like humans, **long-term memory** splits into:
- **Semantic** — facts and knowledge.
- **Episodic** — past experiences / task completions.
- **Procedural** — how to do things (internalized prompts/instructions).

This enables **continual learning** — agents adapt to new tasks **without retraining LLM weights**.

## 6.5 Why Memory Matters for Agentic Systems

- **Without memory**: every interaction is stateless/a blank slate — mentioning your name 5 seconds ago is already forgotten; troubleshooting context from a prior session is gone.
- **With memory**: agent recalls prior iterations → context-aware, practically usable in production.
- Full memory architecture: **Short-Term**, **Long-Term**, **Entity**, **Contextual**, **User** Memory.
- **Key insight**: memory is not a property of the model — it's a **system design problem**: the system must explicitly decide what to keep, what to discard, and what to retrieve before each model call.

## 6.6 5 Agentic AI Design Patterns

1. **Reflection** — AI reviews its own output, spots mistakes, iterates until final response is good.
2. **Tool use** — LLM gathers more info via vector DB queries, Python execution, API calls — reduces sole reliance on internal knowledge.
3. **ReAct (Reason + Act)** — combines reflection + tool use in a loop: **Thought → Action → Observation**, repeating until a solution is reached (mirrors human problem-solving). Most agent frameworks (e.g. CrewAI) use this by default under the hood.
4. **Planning** — instead of solving in one shot, the AI builds a roadmap: subdivides tasks, outlines objectives, then executes (`planning=True` in CrewAI).
5. **Multi-Agent** — several role-specific agents, each with tools, collaborate/delegate to deliver the final outcome.

## 6.7 ReAct — Implementation From Scratch

### Pattern A: Manual step-by-step execution (for debugging/understanding)
```python
from litellm import completion

class MyAgent:
    def __init__(self, system=""):
        self.system = system
        self.messages = []
        if system:
            self.messages.append({"role": "system", "content": system})

    def __call__(self, message=""):
        if message:
            self.messages.append({"role": "user", "content": message})
        result = self.invoke()
        self.messages.append({"role": "assistant", "content": result})
        return result

    def invoke(self):
        response = completion(model="openai/gpt-4o", messages=self.messages)
        return response.choices[0].message.content

REACT_SYSTEM_PROMPT = """
You run in a loop and do JUST ONE thing in a single iteration:
1) "Thought" to describe your thoughts about the input question.
2) "PAUSE" to pause and think about the action to take.
3) "Action" to decide what action to take from the list of actions available to you.
4) "PAUSE" to pause and wait for the result of the action.
5) "Observation" will be the output returned by the action.
At the end of the loop, you produce an Answer.

The actions available to you are:
math:
e.g. math: (14 * 5) / 4
Evaluates mathematical expressions using Python syntax.

lookup_population:
e.g. lookup_population: India
Returns the latest known population of the specified country.

Whenever you have the answer, stop the loop and output it to the user.
Now begin solving:
"""

agent = MyAgent(system=REACT_SYSTEM_PROMPT)
print(agent("What is double the population of Japan?"))
print(agent())  # continue the loop — no new user input
# ... manually feed tool results back as "Observation: <value>" between iterations
```

### Pattern B: Fully automated controller (no manual intervention)
```python
import re

def agent_loop(query, system_prompt, tools: dict, max_iterations=15):
    agent = MyAgent(system=system_prompt)
    current_prompt = query
    for _ in range(max_iterations):
        result = agent(current_prompt)
        print(result)

        if result.startswith("Answer:"):
            return result

        if "Action:" in result:
            action_line = [l for l in result.split("\n") if l.startswith("Action:")][0]
            match = re.match(r"Action:\s*(\w+):\s*(.*)", action_line)
            tool_name, tool_arg = match.group(1), match.group(2)
            if tool_name in tools:
                observation = tools[tool_name](tool_arg)
                current_prompt = f"Observation: {observation}"
            else:
                current_prompt = f"Observation: tool '{tool_name}' not found. Try again."
        else:
            current_prompt = ""  # let it continue thinking / hit next PAUSE

    return "Max iterations reached without a final answer."

def math_tool(expr): return eval(expr)
def lookup_population(country):
    return {"India": 1_400_000_000, "Japan": 125_000_000}.get(country.strip(), "unknown")

tools = {"math": math_tool, "lookup_population": lookup_population}
answer = agent_loop("What is double the population of Japan?", REACT_SYSTEM_PROMPT, tools)
```

**Production hardening notes** (beyond the demo):
- Regex/hardcoded parsing is brittle — prefer **structured output / function calling** (JSON schema) over free-text `Action:` parsing.
- Add tool validation, retries, and exception handling.
- Add guardrails/output formatters constraining what the LLM may emit.
- Cap `max_iterations` to avoid infinite loops; log every Thought/Action/Observation for auditability.

## 6.8 5 Levels of Agentic AI Systems

1. **Basic responder** — human guides the entire flow; LLM just receives input → produces output, no control over program flow.
2. **Router pattern** — human defines fixed paths/functions; LLM makes basic decisions about which path to take.
3. **Tool calling** — human defines a toolset; LLM decides *when* to call tools and *what arguments* to use.
4. **Multi-agent pattern** — a manager agent coordinates sub-agents iteratively; human defines hierarchy/roles/tools; LLM controls execution flow (what happens next).
5. **Autonomous pattern** — LLM generates and executes new code independently, acting as an independent AI developer (most advanced/least constrained).

## 6.9 30 Must-Know Agentic AI Terms (Glossary)

| Term | Definition |
|---|---|
| Agent | Autonomous entity that perceives, reasons, acts toward a goal |
| Environment | The world/system an agent operates in |
| Action | A response/task performed based on reasoning |
| Observation | Data/input received from the environment |
| Goal | The desired outcome |
| LLMs | Enable reasoning + natural language generation |
| Tools | APIs/utilities extending agent capability |
| Evaluation | Assessing performance against goals |
| Orchestration | Coordinating multiple agents for complex tasks |
| Multi-agent system | Group of agents collaborating toward a goal |
| Human-in-the-loop | Human intervenes/guides decision-making |
| Reflection | Self-assessing actions to improve future performance |
| Planning | Determining the step sequence to reach a goal |
| ReAct | Reasoning (thought) + acting (tool use), step by step |
| Feedback loop | Continuous collect→observe→adjust cycle |
| Context window | Max info an agent can consider at once |
| System prompt | Persistent background instructions/personality |
| Few-shot learning | Teaching new behavior via a few examples |
| Hierarchical Agents | Supervisor delegates to sub-agents |
| Short-term memory | Temporary, single-session context |
| Long-term memory | Persistent, cross-session context |
| Knowledge base | Structured info repository for reasoning |
| Context engineering | Shaping what an agent sees to optimize output |
| Guardrails | Rules preventing harmful/undesired actions |
| Tool call | An API invocation to perform a task |
| Guidelines | Policies keeping behavior aligned |
| ARQ | Structured, domain-specific step-by-step reasoning queries |
| MCP | Standardized agent-to-tool/data/API connection |
| A2A | Agent-to-agent communication protocol |
| Router | Directs tasks to the appropriate agent/tool |

## 6.10 4 Layers of Agentic AI

1. **LLMs (foundation)** — tokenization/inference params, prompt engineering, LLM APIs. The engine powering everything else.
2. **AI Agents (built on LLMs)** — tool usage/function calling, agent reasoning (ReAct/CoT), task planning/decomposition, memory management. The brains that make LLMs useful in workflows.
3. **Agentic Systems (multi-agent)** — inter-agent communication (ACP/A2A), routing & scheduling, state coordination, multi-agent RAG, role specialization, orchestration frameworks (CrewAI etc.). Collaboration/coordination layer.
4. **Agentic Infrastructure** — observability/logging, error handling/retries, security/access control, rate limiting/cost management, workflow automation, human-in-the-loop controls. Governance layer ensuring trust/safety/scalability for production.

## 6.11 7 Patterns in Multi-Agent Systems

1. **Parallel** — each agent handles a different subtask concurrently; outputs merged. Reduces latency (document parsing, API orchestration).
2. **Sequential** — each agent adds value step-by-step (code-gen → review → deploy). Workflow automation, ETL chains, multi-step reasoning.
3. **Loop** — agents iteratively refine their own output until a quality bar is met. Proofreading, report generation, creative iteration.
4. **Router** — a controller agent routes tasks to the right specialist (finance query → FinAgent, legal query → LawAgent). Foundation of context-aware routing (MCP/A2A style).
5. **Aggregator** — many agents produce partial results; a central agent combines them into consensus. RAG retrieval fusion, voting systems.
6. **Network** — no hierarchy; agents freely converse, sharing context dynamically. Simulations, multi-agent games, collective reasoning.
7. **Hierarchical** — a top-level planner delegates subtasks to workers, tracks progress, makes final calls (manager + team).

**Design principle**: don't pick the "coolest" pattern — pick the one that **minimizes friction**: no duplicated work, every agent knows when to act/wait, system feels collectively smarter than any individual part.

## 6.12 Agent2Agent (A2A) Protocol

- **MCP** gives agents access to **tools/APIs**. **A2A** lets agents connect to **other agents** — they're complementary, not competing.
- A2A enables agents to collaborate on tasks **without sharing internal memory/thoughts/tools directly** — they exchange context, task updates, instructions, and data instead.
- Agents can be modeled as MCP resources via their **AgentCard**.
- **AgentCard**: a published "JSON Agent Card" detailing an agent's capabilities and authentication — clients use it to discover/select the best agent for a task.
- A2A enables: secure collaboration, task/state management, capability discovery, and cross-framework interoperability (LlamaIndex, CrewAI agents working together).

## 6.13 Agent-User Interaction Protocol (AG-UI)

- **Gap identified**: MCP standardizes Agent↔Tool; A2A standardizes Agent↔Agent — but there was no standard for **Agent↔User** communication.
- **Problem without it**: streaming token-by-token needs custom WebSocket servers; showing live tool progress / pausing for human feedback is hard without losing context; syncing large changing objects (code/tables) means re-sending everything; every framework (LangGraph/CrewAI/Mastra) needs its own custom UI-adapter/JSON format — doesn't scale, and swapping frameworks means rewriting the frontend.
- **AG-UI (by CopilotKit)**: standardizes the backend-agent ↔ frontend-UI interaction layer via **Server-Sent Events (SSE)** streaming structured JSON events:
  - `TEXT_MESSAGE_CONTENT` — token streaming.
  - `TOOL_CALL_START` — show tool execution.
  - `STATE_DELTA` — update shared state (code/data) incrementally.
  - `AGENT_HANDOFF` — pass control between agents smoothly.
- TypeScript + Python SDKs make it plug-and-play. Write backend logic once, hook into AG-UI, and it works across LangGraph/CrewAI/Mastra backends and CopilotKit or custom React frontends — swap the underlying LLM without touching the frontend.

## 6.14 The Agent Protocol Landscape (Converging Stack)

Three complementary (not competing) protocols:
- **AG-UI** — bidirectional Agent↔User (frontend/backend).
- **MCP** — Agent↔Tool/data/workflow (started by Anthropic, now broadly adopted).
- **A2A** — Agent↔Agent multi-agent coordination/delegation.

AG-UI can handshake with both MCP and A2A → tool outputs and multi-agent collaboration flow seamlessly to the UI through one unified layer. **CopilotKit** sits above all three as an "Agentic Application Framework" providing all three protocols + generative UI + production infra in one open-source package.

## 6.15 Agent Optimization With Opik (Automated Prompt Optimization)

- Manual prompt iteration doesn't scale and degrades across models. **Opik Agent Optimizer** automates it: start with an initial prompt + evaluation dataset, let an LLM iteratively critique/refine the prompt against a metric.

```python
import opik
from opik_optimizer import MetaPromptOptimizer
from opik_optimizer.datasets import tiny_test
from opik.evaluation.metrics import LevenshteinRatio

dataset = tiny_test()  # or your own dataset of {input, expected_output} pairs

metric_config = {"metric": LevenshteinRatio()}

base_prompt = "Answer the question concisely: {input}"

optimizer = MetaPromptOptimizer(model="gpt-4o")
result = optimizer.optimize_prompt(
    dataset=dataset, metric_config=metric_config, prompt=base_prompt,
)
result.display()  # best prompt found + score, vs. baseline
```
- Runs 100% locally/self-hosted if desired (Opik is open-source; any LLM can drive optimization).
- Dashboard visualizes iteration history for further analysis.

## 6.16 AI Agent Deployment Strategies — 4 Patterns

1. **Batch deployment** — scheduled automation (like a cron/CLI job); optimizes for **throughput over latency**; processes large data volumes without needing an immediate response.
2. **Stream deployment** — agent embedded in a streaming data pipeline; continuously processes data as it flows, handles concurrent streams, multiple downstream consumers of outputs. Best for continuous monitoring/real-time data processing.
3. **Real-time deployment** — agent runs behind a REST/gRPC API; retrieves context, reasons, responds **instantly**; load balancers scale across concurrent requests. Best for chatbots, virtual assistants, sub-second UX.
4. **Edge deployment** — reasoning logic runs **on-device** (phone/watch/laptop) — no server round-trip; sensitive data never leaves the device (privacy); works offline. Best for privacy-first/offline apps.

**Summary**: Batch = max throughput · Stream = continuous processing · Real-Time = instant interaction · Edge = privacy + offline. Match deployment pattern to latency/cost/privacy requirements, not habit.

---

# PART 7 — Model Context Protocol (MCP)

## 7.1 What Is MCP
- Analogy: without a shared language, you'd need to learn French, German, etc. separately to talk to each person — a **translator** (MCP) lets you speak once and reach everyone.
- **Formal**: MCP is a standardized interface/framework letting AI models seamlessly interact with external tools, resources, and environments — the "USB-C" of AI-to-capability connections.

## 7.2 Why MCP Was Created — The M×N Problem

- Without MCP: M AI applications × N tools/data sources → up to **M×N custom integrations** (spaghetti of one-off code).
- **MCP's fix**: standard interface in the middle → **M+N** implementations. Each AI app implements the MCP **client** side once; each tool implements the MCP **server** side once. New pairings need zero custom code — everyone already "speaks MCP."

## 7.3 MCP Architecture — Host / Client / Server

- **Host** — the user-facing AI app (chat app, AI-IDE, custom app). Initiates connections to MCP servers, keeps conversation history, shows model replies.
- **Client** — a component *within* the Host handling low-level MCP communication (the "adapter/messenger"). Host decides *what*; Client knows *how* to speak MCP.
- **Server** — external program/service providing actual capabilities (tools/resources/prompts). Can run locally or remotely; advertises what it can do in a standard format; executes requests, returns results.

## 7.4 Tools, Resources, and Prompts — the 3 Server Capabilities

| Capability | What it is | Control | Side effects? |
|---|---|---|---|
| **Tools** | Executable actions/functions | Model-controlled (LLM decides to call) | Yes — can write/trigger external effects |
| **Resources** | Read-only data sources | App/host-controlled (fetched when needed) | No — pure retrieval |
| **Prompts** | Predefined instruction templates/workflows | User/developer-controlled | N/A — sets the stage before generation |

```python
# Tool (executable, model-triggered)
@mcp.tool()
def get_weather(location: str) -> dict:
    """Return current weather for a location."""
    ...

# Resource (read-only, app-triggered)
@mcp.resource("file://{path}")
def read_file(path: str) -> str:
    return open(path).read()

# Prompt (predefined template, user-selected)
@mcp.prompt()
def code_review_prompt(code: str) -> list[dict]:
    return [
        {"role": "system", "content": "You are a rigorous code reviewer."},
        {"role": "user", "content": f"Review this code:\n{code}"},
    ]
```
- Since a tool can have side effects (file I/O, network calls), clients often require **user permission** the first time a tool is invoked ("AI wants to use 'get_weather', allow?").

## 7.5 API vs MCP

| | Traditional API | MCP |
|---|---|---|
| Contract changes | Breaking — adding a required param breaks every existing caller; all clients must be manually updated | Non-breaking — client queries the server's **current capabilities** at connect time and adapts dynamically |
| Purpose | General-purpose software↔software | Specifically for AI agents ↔ tools/data, managing dynamic/evolving context |
| Adaptability | Requires pre-programmed integration per API version | Designed for agents that must adapt to new capabilities without pre-programming |

## 7.6 MCP vs Function Calling

- **Function calling**: developers pre-define functions; LLM interprets the prompt and picks a function to call; app executes it and returns result to user.
- **Limitations**: M×N integration growth as functions multiply; functions tightly coupled to one app (hard to reuse across systems); any change needs manual updates everywhere it's used.
- **MCP**: decouples tool **implementation** from tool **consumption** → standardized, modular, scalable integration; a server's tools are automatically discoverable by any MCP client.

## 7.7 The 6 Core MCP Primitives

**Client-side capabilities** (client always has an LLM attached):
1. **Sampling** — server can ask the client's LLM to generate completions (client retains permission/safety control). E.g., server picks the optimal flight from a list by asking the LLM to reason over it.
2. **Roots** — client defines what files/directories the server may access → sandboxed, scoped, secured access (e.g. server can read only a specific calendar directory).
3. **Elicitations** — server can request structured user input mid-task (e.g. ask for seat preference to finalize a booking).

**Server-side capabilities**:
4. **Tools** — model-controlled actions (search flights, send messages, create calendar events, write to DB).
5. **Resources** — app-controlled, passive read-only data (docs, calendars, knowledge bases).
6. **Prompts** — user-controlled instruction templates (plan a vacation, draft an email, summarize meetings).

**Key insight**: MCP is not "just another tool-calling standard" — it's **two-way communication** between AI apps and servers, enabling much richer workflows than function calling alone.

## 7.8 Building with `mcp-use` — Agents, Clients, Servers

### Creating an MCP Agent (6 lines)
```python
from mcp_use import MCPAgent, MCPClient
from langchain_openai import ChatOpenAI

client = MCPClient.from_config_file("browser_mcp.json")  # e.g. Playwright server config
llm = ChatOpenAI(model="gpt-4o")
agent = MCPAgent(llm=llm, client=client)

result = await agent.run("Go to example.com and summarize the homepage.")
```
- Sets up the MCP client, connects to server(s), discovers tools, exposes them as structured tools to the LLM — LLM can call them naturally during reasoning; framework handles execution/streaming.

### Common Pitfall — Tool Overload
1. **Tool-name hallucination** — model invents a nonexistent tool (large/poorly-named tool lists worsen this).
2. **Confusion between similar tools** — overlapping-responsibility tools confuse selection.
3. **Degraded decision quality** — too many tools at once = high cognitive load = inconsistent selection/unneeded calls.

### Fix — Server Manager Pattern
```python
agent = MCPAgent(llm=llm, client=client, use_server_manager=True)
```
- Loads tools **dynamically, only when needed**; discovers the right server for the task; keeps the active toolset small and focused; updates in real time as servers connect/disconnect; provides **semantic search over all available tools** across servers. The Server Manager becomes the orchestrator deciding which server/tools to surface — clearer selection, more stable multi-server behavior.

### Creating an MCP Client
```python
# From config file (good for multi-env / version-controlled settings)
client = MCPClient.from_config_file("mcp_config.json")

# From a Python dict (good for programmatic customization)
client = MCPClient.from_dict({
    "mcpServers": {
        "weather": {"command": "python", "args": ["weather_server.py"]}
    }
})

# Inspect the client's discovered capabilities
tools = await client.get_all_active_tools()
```
Client responsibilities: connect to servers, perform initial capability handshake, stream tool calls/responses, retrieve resources, receive notifications, route elicitation requests back to the user/host.

### Creating an MCP Server (TypeScript, `mcp-use`)
```bash
npx create-mcp-use-app my-server   # scaffolds TS entrypoint, example tools/prompts/resources, config, MCP Inspector support
```

```typescript
// Tool
server.tool("get_weather", { location: z.string() }, async ({ location }) => {
  return { content: [{ type: "text", text: `Weather in ${location}: Sunny, 22°C` }] };
});

// Resource
server.resource("config://app-settings", async () => ({
  contents: [{ uri: "config://app-settings", text: JSON.stringify(settings) }]
}));

// Prompt
server.prompt("plan_trip", { destination: z.string() }, async ({ destination }) => ({
  messages: [{ role: "user", content: { type: "text", text: `Plan a trip to ${destination}` } }]
}));

// Sampling — ask the CLIENT's LLM to generate mid-workflow
const completion = await server.requestSampling({ messages: [...] });

// Elicitation — ask the USER for structured input mid-task
const seatChoice = await server.elicitInput({ prompt: "Window or aisle seat?", schema: seatSchema });

// Notifications — push async progress updates
server.sendNotification({ type: "progress", message: "50% complete" });
```

**MCP Inspector** (`npm run dev`) — web dashboard to browse/test tools interactively, explore resources, preview prompts, watch sampling/notification events live, and monitor all JSON-RPC traffic — the fastest way to verify a server before wiring it to an agent.

**MCP-UI** — lets a server expose small UI widgets (health indicators, resource previews, tool-call results) inside compatible clients, without building a full app.

**OpenAI Apps SDK integration** — `mcp-use` can auto-register React widgets (with a `widgetMetadata` export) as MCP resources/tools, bundle them, and apply required CSP config automatically — no manual HTML/bundling.

**Tunneling (local dev → public endpoint)**
```bash
mcp-use tunnel   # exposes local server at e.g. https://example.local.mcp-use.run/mcp
```
or via the dev runner (`enable_tunnel=True`), or third-party tools like `ngrok`, as long as the public URL maps to the `/mcp` endpoint. Enables testing with real clients (ChatGPT/Claude/mobile agents), remote environments, teammate sharing, pre-deployment validation.

**Deployment** — MCP servers run anywhere Node.js runs: local machines, cloud VMs, Docker, serverless, edge runtimes, or `mcp-use Cloud` (hosted, one-command deploy + public endpoint + live Inspector link, auto-detects GitHub repos). Clients never need reconfiguration after deploy — capability negotiation picks up changes automatically on next connect. Workflow: **update → deploy → capabilities update instantly**.

---

# PART 8 — LLM Optimization

## 8.1 Why Optimization Is Needed
- ML development historically optimizes for **accuracy** → bigger/more complex models.
- But production requires: fast response, cost efficiency, scalability under unpredictable load, strict memory limits.
- A more accurate but slow/huge model (Model A) often loses to a slightly-less-accurate, fast, small, easy-to-deploy model (Model B) in real systems.
- **Model compression** reduces size/compute cost while preserving most performance — makes models production-viable.

## 8.2 Model Compression — 4 Techniques

### 1) Knowledge Distillation
- Train a small **student** model to mimic a larger **teacher** model's behavior.
- Two-step: (1) train/obtain the teacher, (2) train the student to replicate teacher outputs/insights.
- Real example: **DistilBERT** — ~40% smaller than BERT, retains ~97% of BERT's NLU capability, ~60% faster inference.

### 2) Pruning
- Analogy to decision-tree pruning: remove sub-structures that cost little accuracy but save lots of complexity.
- **Neuron pruning** — remove entire nodes → smaller weight matrices → faster inference + lower memory.
- **Weight pruning** — zero out individual edges/weights → matrix stays same size but becomes **sparse** → doesn't necessarily speed up inference, but reduces memory footprint (sparse storage).
- Removing whole layers is rare (misaligns weight matrices, hard to quantify a layer's contribution) — prefer redefining architecture without that layer over post-hoc layer deletion.

### 3) Low-Rank Factorization
- Approximate a weight matrix as the product of two (or more) **lower-rank** matrices.
- Steps: (1) matrix factorization (SVD, NMF, Truncated SVD) on each layer's weight matrix; (2) choose rank `k` (trade-off: smaller k = more compression, more info loss); (3) reconstruct/replace the original matrix with its low-rank approximation for inference — fewer parameters, less compute, most learned structure retained.

### 4) Quantization
- Represent weights with fewer bits: fp32 → fp16/int8/int4/1-bit.
- fp32→int8 ≈ 75% memory reduction while still covering a large value range.
- Trade-off: smaller/faster model vs. some precision loss (approximate predictions vs. full-precision).
- Especially valuable for **edge devices, mobile, specialized hardware**.

```python
# bitsandbytes 8-bit quantized loading (production pattern)
from transformers import AutoModelForCausalLM, BitsAndBytesConfig
import torch

bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.bfloat16,
    bnb_4bit_use_double_quant=True,
)
model = AutoModelForCausalLM.from_pretrained("meta-llama/Llama-3.1-8B", quantization_config=bnb_config)
```

## 8.3 Regular ML Inference vs. LLM Inference — Unique Challenges

### Continuous Batching
- Fixed-shape models (e.g., CNNs) batch trivially. LLMs have **variable-length inputs/outputs** — naive batching makes the GPU wait for the *longest* sequence in the batch, wasting cycles on finished sequences.
- **Fix**: continuously monitor all in-flight sequences; the instant one hits `<EOS>`, swap in a new request → GPU pipeline stays full.

### Prefill-Decode Disaggregation
- LLM inference = two stages with **opposite** resource profiles:
  - **Prefill** — process the whole input prompt at once → **compute-heavy**.
  - **Decode** — autoregressively generate output tokens one at a time → **latency-sensitive**.
- Running both on the same GPU pool → compute-heavy prefill interferes with latency-sensitive decode.
- **Fix**: dedicate separate GPU pools to prefill vs. decode.

### GPU Memory Management + KV Caching
- Generating a new token needs the K/V vectors of **all previous tokens** — recomputing them every step is wasteful → **cache** them (grows linearly with conversation length).
- Shared prefixes (e.g. identical system prompts across requests) can reuse cached KV vectors — avoiding recomputation across requests too.
- Naive contiguous-block KV storage wastes memory via **fragmentation**.
- **Fix — PagedAttention**: store KV cache in small **non-contiguous pages**, tracked via a lookup table; the model loads only the pages it needs (not everything at once) → bigger batches, less fragmentation, longer contexts on the same hardware.

### Prefix-Aware Routing
- Standard ML scaling: replicate the model, load-balance with Round Robin / least-busy — fine since each request is independent.
- LLMs are **not independent** — they rely on cached shared prefixes. If a query's prefix is cached on Replica A but routed to less-busy Replica B, B must recompute the entire prefix's KV cache from scratch.
- **Fix**: router maintains a map/table (or predictive algorithm) of which KV prefixes are cached on which replica, and routes matching queries there.

### Model Sharding Strategies
- Dense models: standard parallelism strategies (data/tensor/pipeline parallel) suffice.
- **MoE models** need **expert parallelism** — experts themselves are split across devices; attention layers are replicated on every GPU. Each GPU holds only some experts' full weights and processes only tokens the gating network routes to those experts. This requires a sophisticated inference engine to manage dynamic routing across the sharded expert pool — cannot be treated like a simple replicated dense model.

## 8.4 KV Caching in LLMs — Deep Mechanism

- Observed speedup: ~9s with KV caching vs ~40s without (~4.5x faster; the gap **grows** as more tokens are generated).
- **Why it works**:
  1. Transformer produces hidden states for all tokens → projected to vocab space → **only the last token's logits** matter for generating the next token.
  2. In attention, the **last row** of `Q·Kᵀ` uses only the last token's query vector, but ALL key vectors. Similarly the final attention output's last row uses the last query and ALL key/value vectors.
  3. **Crucial insight**: KV vectors for all *previously seen* tokens **never change** as generation proceeds → only need to compute the new token's own KV vector; retrieve the rest from cache.
- **Per-step algorithm**: generate Q/K/V for the newest token → fetch all other K/V from cache → compute attention → store the new K/V into the cache for future steps.
- This is why the **first token** takes longer to generate (prompt's full KV cache must be computed) than subsequent tokens.
- **Memory cost is real**: e.g. Llama3-70B (80 layers, 8k hidden size, 4k max output) ≈ 2.5 MB per token in KV cache → 4k tokens ≈ 10.5 GB. More concurrent users = proportionally more memory — a first-order capacity-planning input.

---

# PART 9 — LLM Evaluation

## 9.1 Why Evaluation Matters
- Optimization makes a model fast/cheap; it says nothing about whether the system is **good** — need to measure reasoning, instruction-following, tool use, multi-turn consistency, safety under adversarial pressure.

## 9.2 G-Eval (LLM-as-a-Judge, task-agnostic)
- **Problem**: standard/deterministic metrics fail because LLM outputs vary in form while conveying the same meaning; many criteria can't be formalized as deterministic code.
- **G-Eval**: define evaluation criteria **in plain English**; internally uses **Chain-of-Thought prompting** to build evaluation steps, then returns a score.

```python
from opik.evaluation.metrics import GEval

correctness_metric = GEval(
    task_introduction="You are evaluating whether a response correctly answers a customer support question.",
    evaluation_criteria="The response should be factually correct, directly address the question, and not include unrelated information.",
)
score = correctness_metric.score(output=llm_output, context=retrieved_context)
print(score.value, score.reason)
```
- Self-hostable (data stays private); integrates with CrewAI/LlamaIndex/LangChain/Haystack.

## 9.3 LLM Arena-as-a-Judge (Head-to-Head Comparison)
- **Problem with single-output scoring**: if Prompt A scores 0.72 and Prompt B scores 0.74 in isolation, you still can't be confident B is truly better (subjective scoring, no ground-truth-like objectivity of classic ML metrics like F1/RMSE).
- **Fix**: run **A vs B pairwise comparisons** and pick the better output directly, rather than assigning isolated scores. Define "better" in plain English (helpfulness, conciseness, politeness, etc.), any LLM can act as judge.

```python
from deepeval.test_case import ArenaTestCase, LLMTestCase
from deepeval.metrics import ArenaGEval

arena_case = ArenaTestCase(contestants={
    "Prompt A": LLMTestCase(input=query, actual_output=output_a),
    "Prompt B": LLMTestCase(input=query, actual_output=output_b),
})
metric = ArenaGEval(criteria="Choose the response that is more helpful and concise.")
metric.measure(arena_case)
print(metric.winner)
```
- Can be **referenceless** or **reference-based** (with an expected output supplied).

## 9.4 Multi-Turn Evals for LLM Apps
- Conversations need consistency, compliance, and context-awareness **across turns**, not just accuracy per response.

```python
from deepeval.test_case import ConversationalTestCase, Turn
from deepeval.metrics import ConversationalGEval

convo = ConversationalTestCase(turns=[
    Turn(role="user", content="I lost a lot in the market, what should I invest in next?"),
    Turn(role="assistant", content="I can't give investment advice, but a certified financial advisor could help you plan next steps."),
])
metric = ConversationalGEval(
    criteria="The assistant must avoid giving investment advice and instead direct the user to a professional.",
)
metric.measure(convo)
```
- Produces a pass/fail breakdown per conversation + score distribution + full turn-by-turn UI inspection.

## 9.5 Evaluating MCP-Powered LLM Apps
Two questions determine MCP app quality: **is the right tool selected?** and **is the tool call correctly constructed (arguments)?**

```
1. Integrate MCP server with the LLM app.
2. Send queries; log tool calls + tool outputs.
3. Run the eval → get insight into MCP interaction quality.
```

```python
from deepeval.test_case import LLMTestCase, MCPToolCall
from deepeval.metrics import MCPUseMetric

# after running a query through your MCP-enabled LLM app:
test_case = LLMTestCase(
    input=user_query,
    actual_output=llm_response,
    tools_called=[MCPToolCall(name="search_flights", args={"origin": "SFO", "dest": "JFK"})],
    mcp_servers=[mcp_server],  # exposes available tool list for comparison
)
metric = MCPUseMetric()  # scores (a) capability utilization, (b) argument correctness; final = min(both)
metric.measure(test_case)
```
- Iterating with this metric (e.g. improving tool **docstrings**) took one real example from passing 1–2/24 test cases to **100% success**.

## 9.6 Component-Level Evals for LLM Apps
- Most evals treat the app as a black box (input → output → score). But failures can hide anywhere: retriever, generator, or a specific tool call.

```python
from deepeval.tracing import observe
from deepeval.metrics import AnswerRelevancyMetric, ContextualRelevancyMetric

@observe(metrics=[ContextualRelevancyMetric()])
def retriever(query): ...

@observe(metrics=[AnswerRelevancyMetric()])
def generator(query, context): ...

@observe()  # top-level, traces the whole pipeline
def rag_app(query):
    context = retriever(query)
    return generator(query, context)
```
- 3-step pattern: (1) trace individual components with `@observe`, (2) attach different metrics per component, (3) get a visual breakdown at both test-case and component level — isolates exactly *where* a failure occurs.

## 9.7 Red Teaming LLM Apps
- Correctness/faithfulness metrics ≠ **security**. A well-crafted adversarial prompt can make even a "safe" model leak PII, produce harmful content, or expose internal data. Every major lab treats red teaming as core to development.
- Needs SOTA adversarial strategies: **prompt injection, jailbreaking, response manipulation**, etc., plus prompts mimicking real attackers, evaluated against PII leakage, bias, toxicity, unauthorized access, harmful-content generation.
- Single-turn tests target immediate jailbreaks; **multi-turn tests** simulate conversational grooming/trust-building manipulation.

```python
from deepteam import red_team
from deepteam.vulnerabilities import Bias, Toxicity
from deepteam.attacks import PromptInjection

def model_callback(prompt: str) -> str:
    return my_llm_app(prompt)

vulnerabilities = [Bias(types=["race", "gender"]), Toxicity(types=["insults", "threats"])]
attacks = [PromptInjection()]

risk_assessment = red_team(model_callback=model_callback, vulnerabilities=vulnerabilities, attacks=attacks)
print(risk_assessment.overview())  # pass/fail per vulnerability, prompts used, reasons
```
- No hand-built dataset required — adversarial attacks are **dynamically simulated at run-time** based on the vulnerabilities you specify. Comes with **guardrails** to prevent issues once vulnerabilities are found, and integrates with dashboards (e.g. Confident AI) for logging.
- **Core insight**: LLM security is a **red-teaming problem, not a benchmarking problem** — think like an attacker from day one.

---

# PART 10 — LLM Deployment

## 10.1 Why LLM Deployment Is Different
Traditional ML inference is a single, uniform compute phase. LLMs need **two things**:
1. An inference engine that executes tokens efficiently (handles the challenges from §8.3).
2. A deployment strategy that scales without manual infra babysitting.

## 10.2 vLLM — Production Inference Engine
Solves 3 core problems:
- **Underutilized GPUs** → **Continuous batching**: removes finished sequences (`<EOS>`) immediately, fills empty slots with new requests — GPU pipeline stays full with zero code changes needed.
- **Wasteful KV-cache memory** → **PagedAttention**: stores KV cache in small non-contiguous pages + lookup table → supports larger batches, avoids fragmentation, serves longer contexts on the same hardware.
- **Difficult DX** → **OpenAI-compatible API**: migrating an app is often just changing `base_url`.

Additional built-in capabilities:
- **Smart scheduling** — automatically balances throughput-heavy prefill vs latency-sensitive decode (groups prefill work, interleaves decode steps).
- **Prefix-aware routing** — keeps prefix-sharing sequences on the same worker, avoiding KV recompute.
- **LoRA + multi-model support** — loads LoRA adapters once, applies per-request without duplicating memory; can host multiple base models in one server (personalization, A/B testing, multi-feature apps).

```bash
# 1) Start the vLLM server (OpenAI-compatible)
python -m vllm.entrypoints.openai.api_server --model meta-llama/Llama-3.1-8B-Instruct

# 3) Scale across multiple GPUs
python -m vllm.entrypoints.openai.api_server --model meta-llama/Llama-3.1-70B-Instruct --tensor-parallel-size 4

# 4) Serve LoRA adapters alongside the base model
python -m vllm.entrypoints.openai.api_server --model meta-llama/Llama-3.1-8B \
    --enable-lora --lora-modules sql-adapter=./lora/sql customer-adapter=./lora/support

# 6) Serve multiple base models from one server
python -m vllm.entrypoints.openai.api_server --model meta-llama/Llama-3.1-8B --served-model-name llama8b
```
```python
# 2) Send requests with the standard OpenAI client
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8000/v1", api_key="not-needed")
resp = client.chat.completions.create(model="meta-llama/Llama-3.1-8B-Instruct",
                                       messages=[{"role": "user", "content": "Explain PagedAttention."}])
```
- **Continuous batching happens automatically** — no config needed as requests arrive concurrently.

## 10.3 LitServe — Custom Inference/App Server Layer
- Real deployments need more than raw model serving: request validation, pre/post-processing, custom routing, auth, logging, monitoring — usually wrapped around the model in a broader **application server**.
- **LitServe**: open-source framework giving full control over batching/streaming/routing/multi-model coordination; works across text, vision, audio, multimodal.

```python
import litserve as ls

class LlamaAPI(ls.LitAPI):
    def setup(self, device):
        # 1) load model once when server starts
        from transformers import pipeline
        self.pipe = pipeline("text-generation", model="meta-llama/Llama-3.1-8B-Instruct", device=device)

    def decode_request(self, request):
        # 2) extract what the model needs from incoming JSON
        return request["prompt"]

    def predict(self, prompt):
        # 3) run inference — can `yield` to stream tokens
        for chunk in self.pipe(prompt, stream=True):
            yield chunk["generated_text"]

    def encode_response(self, output):
        # 4) wrap each streamed chunk into the response JSON
        return {"token": output}

if __name__ == "__main__":
    # 5) launch the HTTP server
    api = LlamaAPI()
    server = ls.LitServer(api, accelerator="auto")
    server.run(port=8000)
```
- Each of the 5 lifecycle methods maps to one deployment concern: `setup` (load once), `decode_request` (parse input), `predict` (inference, streaming-capable), `encode_response` (format output), `run` (expose as HTTP endpoint).

---

# PART 11 — LLM Observability

## 11.1 Evaluation vs Observability

| | **Evaluation** | **Observability** |
|---|---|---|
| Question answered | "Is the model good?" | "What is actually happening inside the system, right now?" |
| Data | Curated datasets, controlled tests | Real production inputs/outputs |
| Timing | Pre-deployment / periodic offline benchmarking | Continuous, live, post-deployment |
| Measures | Correctness, relevance, factuality, safety | Latency, cost, component traces, drift, failure cases, regressions |

Evaluation establishes **expectations**; observability reveals whether those expectations **hold under real operating conditions**. They're complementary, not substitutes.

## 11.2 Implementing Observability (Opik pattern)

### Tracking a plain Python function
```python
from opik import track

@track
def summarize(text: str) -> str:
    return my_llm_call(f"Summarize: {text}")

summarize("...")  # every call auto-logged: inputs, outputs, latency, in the Opik dashboard
```

### Tracking LLM calls directly (OpenAI / Ollama / any OpenAI-compatible endpoint)
```python
from opik.integrations.openai import track_openai
from openai import OpenAI

client = track_openai(OpenAI())  # every call through this client is now auto-logged
response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Describe this image.", }],
    max_tokens=300,
)
# Dashboard shows: input, output, token counts, cost, latency per call

# Local models via Ollama — same pattern, just point base_url at the local server
local_client = track_openai(OpenAI(base_url="http://localhost:11434/v1", api_key="ollama"))
```
- Production value: every input/output/intermediate detail logged automatically — no custom logging boilerplate — enabling debugging of simple functions, LLM calls, or full RAG/agent pipelines (combine with the `@observe` component-level tracing pattern from §9.6 for multi-step systems).

---

# APPENDIX — Quick-Reference Decision Cheatsheets

### A. Adaptation Method Selector
```
Need external/fresh knowledge?  Need behavior/style change?
No / No   → Prompt engineering
Yes / No  → RAG
No / Yes  → Fine-tuning (LoRA-family for efficiency)
Yes / Yes → Hybrid (RAG + Fine-tuning), consider CAG for stable knowledge
```

### B. Fine-Tuning Method Selector
```
Have labeled data?
 No  → Task verifiable? → Yes: RFT (GRPO/RLVR) | No: RLHF
 Yes → Dataset size? → Large: SFT | Tiny: reasoning helps? Yes: RFT | No: SFT
Always prefer PEFT (LoRA/QLoRA/DoRA) over full fine-tuning at LLM scale.
```

### C. RAG Chunking Selector
```
Uniform simple docs, speed matters      → Fixed-size (with overlap)
Natural language flow matters most      → Semantic chunking
Mix of structure + size constraints     → Recursive chunking
Clearly structured docs (headings)      → Document-structure-based
Budget for max semantic accuracy        → LLM-based chunking
```

### D. Multi-Agent Pattern Selector
```
Independent subtasks, need speed        → Parallel
Strict step order, each adds value      → Sequential
Need iterative quality improvement      → Loop
Route to the right specialist           → Router
Combine several partial opinions        → Aggregator
No hierarchy, free-form collaboration   → Network
Manager + delegated workers             → Hierarchical
```

### E. Agent Deployment Selector
```
Large batch, no immediate response      → Batch
Continuous real-time data flow          → Stream
Instant user-facing response needed     → Real-Time (API + load balancer)
Privacy-critical / must work offline    → Edge
```

### F. Inference-Serving Checklist (Production LLM API)
```
[ ] Continuous batching enabled (vLLM default)
[ ] PagedAttention / KV-cache paging in place
[ ] Prefix-aware routing if using shared system prompts across many requests
[ ] Prefill/decode disaggregation if latency SLAs are strict
[ ] LoRA adapters loaded once, shared across requests (not duplicated per user)
[ ] Quantization (4/8-bit) evaluated for cost/latency vs. accuracy trade-off
[ ] Observability wired in (per-call + per-component tracing)
[ ] Red-teamed before go-live; guardrails configured
[ ] Multi-turn + MCP-tool evals passing, not just single-turn correctness
```
