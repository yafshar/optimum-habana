#!/usr/bin/env python
# coding=utf-8
# Copyright 2024 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and

# Copied from https://huggingface.co/facebook/sam-vit-base

import argparse

import habana_frameworks.torch as ht
import requests
import torch
from PIL import Image
from transformers import AutoModel, AutoProcessor

from optimum.habana.transformers.modeling_utils import adapt_transformers_to_gaudi
from optimum.habana.utils import HabanaGenerationTime


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--model_name_or_path",
        default="facebook/sam-vit-huge",
        type=str,
        help="Path of the pre-trained model",
    )
    parser.add_argument(
        "--image_path",
        default="https://huggingface.co/ybelkada/segment-anything/resolve/main/assets/car.png",
        type=str,
        help='Path of the input image. Should be a single string (eg: --image_path "URL")',
    )
    parser.add_argument(
        "--point_prompt",
        default="450, 600",
        type=str,
        help='Prompt for segmentation. It should be a string separated by comma. (eg: --point_prompt "450, 600")',
    )
    parser.add_argument(
        "--use_hpu_graphs",
        action="store_true",
        help="Whether to use HPU graphs or not. Using HPU graphs should give better latencies.",
    )
    parser.add_argument(
        "--bf16",
        action="store_true",
        help="Whether to use bf16 precision for classification.",
    )
    parser.add_argument(
        "--print_result",
        action="store_true",
        help="Whether to save the segmentation result.",
    )
    parser.add_argument("--warmup", type=int, default=3, help="Number of warmup iterations for benchmarking.")
    parser.add_argument("--n_iterations", type=int, default=5, help="Number of inference iterations for benchmarking.")

    args = parser.parse_args()

    adapt_transformers_to_gaudi()

    processor = AutoProcessor.from_pretrained(args.model_name_or_path)
    model = AutoModel.from_pretrained(args.model_name_or_path)

    image = Image.open(requests.get(args.image_path, stream=True).raw).convert("RGB")
    points = []
    for text in args.point_prompt.split(","):
        points.append(int(text))
    points = [[points]]

    if args.use_hpu_graphs:
        model = ht.hpu.wrap_in_hpu_graph(model)

    autocast = torch.autocast(device_type="hpu", dtype=torch.bfloat16, enabled=args.bf16)
    model.to("hpu")

    with torch.no_grad(), autocast:
        for i in range(args.warmup):
            inputs = processor(image, input_points=points, return_tensors="pt").to("hpu")
            outputs = model(**inputs)
            torch.hpu.synchronize()

        total_model_time = 0
        for i in range(args.n_iterations):
            inputs = processor(image, input_points=points, return_tensors="pt").to("hpu")
            with HabanaGenerationTime() as timer:
                outputs = model(**inputs)
                torch.hpu.synchronize()
            total_model_time += timer.last_duration

            if args.print_result:
                if i == 0:  # generate/output once only
                    iou = outputs.iou_scores
                    print("iou score: " + str(iou))

    print("n_iterations: " + str(args.n_iterations))
    print("Total latency (ms): " + str(total_model_time * 1000))
    print("Average latency (ms): " + str(total_model_time * 1000 / args.n_iterations))
