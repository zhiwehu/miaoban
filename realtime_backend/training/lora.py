"""LoRA deltas on frozen linear layers, with portable safetensors artifacts."""
import math
import torch
from torch import nn


class LoRALinear(nn.Module):
    def __init__(self, base, rank=16, alpha=32, dropout=.05):
        super().__init__()
        self.base = base
        self.base.requires_grad_(False)
        self.rank, self.alpha = rank, alpha
        self.scale = alpha/rank
        self.dropout = nn.Dropout(dropout)
        self.lora_A = nn.Parameter(base.weight.new_empty(rank, base.in_features))
        self.lora_B = nn.Parameter(base.weight.new_zeros(base.out_features, rank))
        nn.init.kaiming_uniform_(self.lora_A, a=math.sqrt(5))

    def forward(self, value):
        return self.base(value) + nn.functional.linear(
            nn.functional.linear(self.dropout(value), self.lora_A), self.lora_B)*self.scale

    def merged(self):
        result = nn.Linear(self.base.in_features, self.base.out_features,
                           bias=self.base.bias is not None, device=self.base.weight.device,
                           dtype=self.base.weight.dtype)
        with torch.no_grad():
            result.weight.copy_(self.base.weight + self.lora_B @ self.lora_A * self.scale)
            if self.base.bias is not None:
                result.bias.copy_(self.base.bias)
        return result


def inject(model, rank=16, alpha=32, dropout=.05):
    model.requires_grad_(False)
    targets = []
    for name, module in list(model.named_modules()):
        if isinstance(module, nn.Linear) and name.rsplit('.',1)[-1] in (
                'query_proj','key_proj','value_proj','ffnq','ffnk'):
            parent_name, _, child = name.rpartition('.')
            parent = model.get_submodule(parent_name) if parent_name else model
            setattr(parent, child, LoRALinear(module, rank, alpha, dropout))
            targets.append(name)
    if not targets:
        raise ValueError('No LoRA target layers')
    return targets


def adapter_state(model):
    return {name:value.detach().cpu().contiguous().clone() for name,value in model.state_dict().items()
            if name.endswith(('lora_A','lora_B'))}


def load_adapter(model, state):
    expected = set(adapter_state(model))
    if set(state) != expected:
        raise ValueError('Adapter keys do not match model')
    for name, value in state.items():
        if value.shape != model.state_dict()[name].shape:
            raise ValueError('Adapter tensor shape mismatch')
    model.load_state_dict(state, strict=False)


def merge(model):
    for name, module in list(model.named_modules()):
        if isinstance(module, LoRALinear):
            parent_name, _, child = name.rpartition('.')
            parent = model.get_submodule(parent_name) if parent_name else model
            setattr(parent, child, module.merged())
    return model
