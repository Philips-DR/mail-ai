"""mail-ai: a correct Gmail client with a hard send gate.

Not an email AI. Triage, drafting and summarising are judgment and live in one declared
seam; everything below it -- sync, threading, MIME, label state -- is mechanical and
model-free, because a model that drifts on a message id sends mail to the wrong person.
"""
