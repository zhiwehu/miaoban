"""Local meeting minutes built on the finished recording transcript."""
from .generator import MinutesService
from .inference import MinutesEngine, MinutesError

__all__ = ['MinutesEngine', 'MinutesError', 'MinutesService']
