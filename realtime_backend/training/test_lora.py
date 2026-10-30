import unittest
import torch
from torch import nn
from .lora import LoRALinear, inject, adapter_state, load_adapter, merge


class LoRATests(unittest.TestCase):
    def test_zero_delta_preserves_base_and_only_adapter_receives_gradients(self):
        torch.manual_seed(17)
        base = nn.Linear(8, 5)
        x = torch.randn(3, 8)
        expected = base(x).detach()
        layer = LoRALinear(base, rank=2, dropout=0)
        torch.testing.assert_close(layer(x), expected)
        layer(x).square().sum().backward()
        self.assertIsNone(base.weight.grad)
        self.assertGreater(layer.lora_B.grad.abs().sum().item(), 0)

    def test_adapter_reload_and_merge_preserve_predictions(self):
        torch.manual_seed(18)
        model = nn.Module();model.ffnq = nn.Linear(8,5)
        original = model.ffnq.state_dict()
        inject(model, rank=2, dropout=0)
        with torch.no_grad(): model.ffnq.lora_B.normal_(std=.1)
        x = torch.randn(3,8)
        expected = model.ffnq(x).detach()
        state = adapter_state(model)
        other = nn.Module();other.ffnq = nn.Linear(8,5)
        other.ffnq.load_state_dict(original)
        inject(other, rank=2, dropout=0)
        load_adapter(other,state)
        torch.testing.assert_close(other.ffnq(x),expected)
        merge(other)
        torch.testing.assert_close(other.ffnq(x),expected,rtol=1e-5,atol=1e-6)
        self.assertIsInstance(other.ffnq,nn.Linear)

    def test_invalid_adapter_cannot_silently_partially_load(self):
        model=nn.Module();model.ffnq=nn.Linear(8,5);inject(model,rank=2)
        with self.assertRaises(ValueError):load_adapter(model,{})
