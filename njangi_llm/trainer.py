from __future__ import annotations

import os
import json
import time
from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim

ART = Path("artifacts")
LOG = ART / "logs" / "train.log"
CKPT_DIR = ART / "checkpoints"
STATE = ART / "state.json"
DATA = ART / "data" / "train.txt"
VOCAB = ART / "data" / "vocab.json"


# -----------------------------
# Tiny tokenizer (character-level starter)
# Replace with SentencePiece later
# -----------------------------
def build_vocab(text: str):
    chars = sorted(list(set(text)))
    stoi = {ch: i for i, ch in enumerate(chars)}
    itos = {i: ch for ch, i in stoi.items()}
    return stoi, itos


def encode(s: str, stoi: dict[str, int]):
    return torch.tensor([stoi[c] for c in s if c in stoi], dtype=torch.long)


# -----------------------------
# Tiny GPT-ish model
# -----------------------------
class TinyLM(nn.Module):
    def __init__(
        self,
        vocab_size: int,
        n_emb: int = 128,
        n_head: int = 4,
        n_layer: int = 2,
        block_size: int = 256,
    ):
        super().__init__()
        self.block_size = block_size
        self.tok = nn.Embedding(vocab_size, n_emb)
        self.pos = nn.Embedding(block_size, n_emb)
        enc = nn.TransformerEncoderLayer(d_model=n_emb, nhead=n_head, batch_first=True)
        self.tr = nn.TransformerEncoder(enc, num_layers=n_layer)
        self.lm_head = nn.Linear(n_emb, vocab_size)

    def forward(self, idx, targets=None):
        B, T = idx.shape
        if T > self.block_size:
            idx = idx[:, -self.block_size :]
            T = idx.shape[1]

        pos = torch.arange(0, T, device=idx.device).unsqueeze(0)
        x = self.tok(idx) + self.pos(pos)
        x = self.tr(x)
        logits = self.lm_head(x)

        loss = None
        if targets is not None:
            targets = targets[:, -T:]
            loss = nn.functional.cross_entropy(
                logits.reshape(-1, logits.size(-1)),
                targets.reshape(-1),
            )
        return logits, loss


def log_line(msg: str):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg.rstrip() + "\n")


def save_state(d: dict):
    ART.mkdir(parents=True, exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


def main():
    CKPT_DIR.mkdir(parents=True, exist_ok=True)
    (ART / "data").mkdir(parents=True, exist_ok=True)

    if not DATA.exists():
        raise SystemExit(f"Missing {DATA}. Create artifacts/data/train.txt first.")

    text = DATA.read_text(encoding="utf-8", errors="ignore")
    if len(text) < 2000:
        log_line("WARNING: train.txt is very small; add more Njangi text for better results.")

    stoi, itos = build_vocab(text)
    vocab_size = len(stoi)

    with open(VOCAB, "w", encoding="utf-8") as f:
        json.dump({"stoi": stoi, "itos": itos}, f)

    data = encode(text, stoi)

    device = "cuda" if torch.cuda.is_available() else "cpu"

    # Hyperparameters (small so it can run on CPU)
    block_size = int(os.environ.get("NJANGI_BLOCK_SIZE", "256"))
    batch_size = int(os.environ.get("NJANGI_BATCH_SIZE", "16" if device == "cuda" else "4"))
    lr = float(os.environ.get("NJANGI_LR", "3e-4"))
    max_steps = int(os.environ.get("NJANGI_TRAIN_STEPS", "2000"))
    ckpt_every = int(os.environ.get("NJANGI_CKPT_EVERY", "200"))

    # Model size (can be tuned)
    n_emb = int(os.environ.get("NJANGI_N_EMB", "128"))
    n_head = int(os.environ.get("NJANGI_N_HEAD", "4"))
    n_layer = int(os.environ.get("NJANGI_N_LAYER", "2"))

    model = TinyLM(vocab_size=vocab_size, n_emb=n_emb, n_head=n_head, n_layer=n_layer, block_size=block_size).to(device)
    opt = optim.AdamW(model.parameters(), lr=lr)

    def get_batch():
        if len(data) < block_size + 2:
            raise SystemExit("train.txt too small for current block_size. Add more text or lower NJANGI_BLOCK_SIZE.")
        ix = torch.randint(0, len(data) - block_size - 1, (batch_size,))
        x = torch.stack([data[i : i + block_size] for i in ix]).to(device)
        y = torch.stack([data[i + 1 : i + block_size + 1] for i in ix]).to(device)
        return x, y

    start = time.time()
    log_line(f"TRAIN START device={device} steps={max_steps} batch={batch_size} block={block_size} emb={n_emb} head={n_head} layer={n_layer}")

    for step in range(1, max_steps + 1):
        model.train()
        x, y = get_batch()
        _, loss = model(x, y)

        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()

        if step % 10 == 0:
            msg = f"step={step} loss={loss.item():.4f} device={device}"
            log_line(msg)
            save_state({"step": step, "loss": float(loss.item()), "device": device, "updated_at": time.time()})

        if step % ckpt_every == 0:
            ckpt_path = CKPT_DIR / f"ckpt_step{step}.pt"
            torch.save(
                {
                    "model": model.state_dict(),
                    "step": step,
                    "vocab_size": vocab_size,
                    "block_size": block_size,
                    "n_emb": n_emb,
                    "n_head": n_head,
                    "n_layer": n_layer,
                },
                ckpt_path,
            )
            log_line(f"saved {ckpt_path.as_posix()}")

    elapsed = time.time() - start
    log_line(f"TRAIN DONE steps={max_steps} elapsed_s={elapsed:.1f}")
    save_state({"step": max_steps, "done": True, "elapsed_s": elapsed, "updated_at": time.time()})


if __name__ == "__main__":
    main()
