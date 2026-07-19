"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

memory/embeddings.py — Motor de embeddings de ClawLite.

Usa all-MiniLM-L6-v2 vía ONNX Runtime (sin PyTorch). Esto hace el proyecto
empaquetable a tamaño razonable (~200MB vs ~2GB con torch) sin perder calidad:
los vectores ONNX son idénticos a los de PyTorch (verificado, similitud 1.0).

El contrato público es estable: encode(text) -> list[float] de 384 dims.
Cambie lo que cambie por dentro, los consumidores (store, multimodal, workflows,
skill_store) no se tocan.
"""

import numpy as np
from loguru import logger

EMBEDDING_DIM = 384
_MODEL_REPO = "sentence-transformers/all-MiniLM-L6-v2"


class EmbeddingEngine:
    """
    Motor de embeddings con carga lazy. Una sola instancia se inyecta a todos
    los consumidores — fuente única del modelo en memoria.
    """

    def __init__(self):
        self._session = None      # ONNX InferenceSession
        self._tokenizer = None    # tokenizers.Tokenizer
        self._input_names = None  # nombres reales de inputs del modelo

    def _ensure_loaded(self):
        """Carga lazy: descarga (cacheado) y prepara el modelo ONNX al primer uso."""
        if self._session is not None:
            return

        logger.info("🧠 Cargando modelo de embeddings ONNX (primera vez)...")
        from huggingface_hub import hf_hub_download
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_path = hf_hub_download(_MODEL_REPO, "onnx/model.onnx")
        tok_path = hf_hub_download(_MODEL_REPO, "tokenizer.json")

        self._tokenizer = Tokenizer.from_file(tok_path)
        self._session = ort.InferenceSession(
            model_path, providers=["CPUExecutionProvider"]
        )
        # Inputs reales que espera el modelo (input_ids, attention_mask, token_type_ids)
        self._input_names = {i.name for i in self._session.get_inputs()}

    def encode(self, text: str) -> list[float]:
        """
        Genera el embedding de un texto. Devuelve list[float] de EMBEDDING_DIM.
        Mean-pooling sobre los tokens + normalización L2 (idéntico a sentence-transformers).
        """
        self._ensure_loaded()

        enc = self._tokenizer.encode(text or "")
        ids = np.array([enc.ids], dtype=np.int64)
        mask = np.array([enc.attention_mask], dtype=np.int64)

        inputs = {"input_ids": ids, "attention_mask": mask}
        # Algunos modelos requieren token_type_ids; lo añadimos solo si el modelo lo pide
        if "token_type_ids" in self._input_names:
            inputs["token_type_ids"] = np.zeros_like(ids)

        out = self._session.run(None, inputs)
        token_emb = out[0][0]  # (n_tokens, 384)

        # Mean-pooling ponderado por la máscara de atención
        m = mask[0][:, None]
        pooled = (token_emb * m).sum(0) / np.clip(m.sum(), 1e-9, None)

        # Normalización L2 (sentence-transformers normaliza por defecto)
        norm = np.linalg.norm(pooled)
        if norm > 0:
            pooled = pooled / norm

        return pooled.astype(float).tolist()