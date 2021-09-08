import math
import os
from argparse import ArgumentParser

import numpy as np
import torch
from pytorch_lightning import Trainer
from pytorch_lightning import seed_everything
from pytorch_lightning.utilities import rank_zero_info
from torch.utils.data import Dataset, DataLoader

from mingpt.callback import CUDACallback
from mingpt.lr_decay import LearningRateDecayCallback
from mingpt.model import GPT


class CharDataset(Dataset):

    def __init__(self, data, block_size):
        chars = list(set(data))
        data_size, vocab_size = len(data), len(chars)
        rank_zero_info('data has %d characters, %d unique.' % (data_size, vocab_size))

        self.stoi = {ch: i for i, ch in enumerate(chars)}
        self.itos = {i: ch for i, ch in enumerate(chars)}
        self.block_size = block_size
        self.vocab_size = vocab_size
        self.data = data

    def __len__(self):
        return math.ceil(len(self.data) / (self.block_size + 1))

    def __getitem__(self, idx):
        # we're actually going to "cheat" and pick a spot in the dataset at random
        i = np.random.randint(0, len(self.data) - (self.block_size + 1))
        chunk = self.data[i:i + self.block_size + 1]
        dix = [self.stoi[s] for s in chunk]
        x = torch.tensor(dix[:-1], dtype=torch.long)
        y = torch.tensor(dix[1:], dtype=torch.long)
        return x, y


if __name__ == '__main__':
    seed_everything(42)

    parser = ArgumentParser()
    parser = Trainer.add_argparse_args(parser)
    parser.add_argument('--n_layer', default=22, type=int)
    parser.add_argument('--n_head', default=16, type=int)
    parser.add_argument('--n_embd', default=3072, type=int)
    parser.add_argument('--learning_rate', default=6e-4, type=float)
    parser.add_argument('--block_size', default=128, type=int)
    parser.add_argument('--batch_size', default=8, type=int)
    parser.add_argument('--num_workers', default=0, type=int)
    parser.add_argument('--checkpoint', action='store_true', default=True)
    parser.add_argument('--auto_wrap', action='store_true', default=False)
    parser.add_argument('--wrap', action='store_true', default=False)
    parser.add_argument('--full_shakespeare', action='store_true', default=False)
    args = parser.parse_args()

    if args.full_shakespeare and not os.path.exists("shakespeare_input.txt"):
        os.system("wget https://cs.stanford.edu/people/karpathy/char-rnn/shakespeare_input.txt")
    elif not os.path.exists("input.txt"):
        os.system("wget https://raw.githubusercontent.com/karpathy/char-rnn/master/data/tinyshakespeare/input.txt")

    file = 'shakespeare_input.txt' if args.full_shakespeare else 'input.txt'
    text = open(file, 'r').read()
    train_dataset = CharDataset(text, args.block_size)  # one line of poem is roughly 50 characters
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, num_workers=args.num_workers)

    model = GPT(
        vocab_size=train_dataset.vocab_size,
        block_size=train_dataset.block_size,
        n_layer=args.n_layer,
        n_head=args.n_head,
        n_embd=args.n_embd,
        learning_rate=args.learning_rate,
        checkpoint=args.checkpoint,
        should_auto_wrap=args.auto_wrap,
        should_wrap=args.wrap
    )

    lr_decay = LearningRateDecayCallback(
        learning_rate=6e-4,
        warmup_tokens=512 * 20,
        final_tokens=2 * len(train_dataset) * args.block_size
    )
    trainer = Trainer.from_argparse_args(
        args,
        plugins=args.plugins,
        log_every_n_steps=1,
        max_epochs=3,
        gradient_clip_val=1.0,
        callbacks=[lr_decay, CUDACallback()],
    )
    trainer.fit(model, train_loader)
    trainer.test(model, train_loader)
    