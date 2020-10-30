# Copyright (c) 2020 PaddlePaddle Authors. All Rights Reserve.
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
# limitations under the License.

import paddle
import paddle.nn as nn
import paddle.nn.functional as F

import functools
import numpy as np

from ...modules.norm import build_norm_layer

from .builder import GENERATORS


class PONO(paddle.nn.Layer):
    def __init__(self, eps=1e-5):
        super(PONO, self).__init__()
        self.eps = eps

    def forward(self, x):
        mean = paddle.mean(x, axis=1, keepdim=True)
        var = paddle.mean(paddle.square(x - mean), axis=1, keepdim=True)
        tmp = (x - mean) / paddle.sqrt(var + self.eps)

        return tmp


class ResidualBlock(paddle.nn.Layer):
    """Residual Block with instance normalization."""
    def __init__(self, dim_in, dim_out, mode=None):
        super(ResidualBlock, self).__init__()
        if mode == 't':
            weight_attr = False
            bias_attr = False
        elif mode == 'p' or (mode is None):
            weight_attr = None
            bias_attr = None

        self.main = nn.Sequential(
            nn.Conv2D(dim_in,
                      dim_out,
                      kernel_size=3,
                      stride=1,
                      padding=1,
                      bias_attr=False),
            nn.InstanceNorm2D(dim_out,
                              weight_attr=weight_attr,
                              bias_attr=bias_attr), nn.ReLU(),
            nn.Conv2D(dim_out,
                      dim_out,
                      kernel_size=3,
                      stride=1,
                      padding=1,
                      bias_attr=False),
            nn.InstanceNorm2D(dim_out,
                              weight_attr=weight_attr,
                              bias_attr=bias_attr))

    def forward(self, x):
        """forward"""
        return x + self.main(x)


class StyleResidualBlock(paddle.nn.Layer):
    """Residual Block with instance normalization."""
    def __init__(self, dim_in, dim_out):
        super(StyleResidualBlock, self).__init__()
        self.block1 = nn.Sequential(
            nn.Conv2D(dim_in,
                      dim_out,
                      kernel_size=3,
                      stride=1,
                      padding=1,
                      bias_attr=False), PONO())
        ks = 3
        pw = ks // 2
        self.beta1 = nn.Conv2D(dim_in, dim_out, kernel_size=ks, padding=pw)
        self.gamma1 = nn.Conv2D(dim_in, dim_out, kernel_size=ks, padding=pw)
        self.block2 = nn.Sequential(
            nn.ReLU(),
            nn.Conv2D(dim_out,
                      dim_out,
                      kernel_size=3,
                      stride=1,
                      padding=1,
                      bias_attr=False), PONO())
        self.beta2 = nn.Conv2D(dim_in, dim_out, kernel_size=ks, padding=pw)
        self.gamma2 = nn.Conv2D(dim_in, dim_out, kernel_size=ks, padding=pw)

    def forward(self, x, y):
        """forward"""
        x_ = self.block1(x)
        b = self.beta1(y)
        g = self.gamma1(y)
        x_ = (g + 1) * x_ + b
        x_ = self.block2(x_)
        b = self.beta2(y)
        g = self.gamma2(y)
        x_ = (g + 1) * x_ + b
        return x + x_


class MDNet(paddle.nn.Layer):
    """MDNet in PSGAN"""
    def __init__(self, conv_dim=64, repeat_num=3):
        super(MDNet, self).__init__()

        layers = []
        layers.append(
            nn.Conv2D(3,
                      conv_dim,
                      kernel_size=7,
                      stride=1,
                      padding=3,
                      bias_attr=False))
        layers.append(
            nn.InstanceNorm2D(conv_dim, weight_attr=None, bias_attr=None))

        layers.append(nn.ReLU())

        # Down-Sampling
        curr_dim = conv_dim
        for i in range(2):
            layers.append(
                nn.Conv2D(curr_dim,
                          curr_dim * 2,
                          kernel_size=4,
                          stride=2,
                          padding=1,
                          bias_attr=False))
            layers.append(
                nn.InstanceNorm2D(curr_dim * 2,
                                  weight_attr=None,
                                  bias_attr=None))
            layers.append(nn.ReLU())
            curr_dim = curr_dim * 2

        # Bottleneck
        for i in range(repeat_num):
            layers.append(ResidualBlock(dim_in=curr_dim, dim_out=curr_dim))

        self.main = nn.Sequential(*layers)

    def forward(self, x):
        """forward"""
        out = self.main(x)
        return out


class TNetDown(paddle.nn.Layer):
    """MDNet in PSGAN"""
    def __init__(self, conv_dim=64, repeat_num=3):
        super(TNetDown, self).__init__()

        layers = []
        layers.append(
            nn.Conv2D(3,
                      conv_dim,
                      kernel_size=7,
                      stride=1,
                      padding=3,
                      bias_attr=False))
        layers.append(
            nn.InstanceNorm2D(conv_dim, weight_attr=False, bias_attr=False))

        layers.append(nn.ReLU())

        # Down-Sampling
        curr_dim = conv_dim
        for i in range(2):
            layers.append(
                nn.Conv2D(curr_dim,
                          curr_dim * 2,
                          kernel_size=4,
                          stride=2,
                          padding=1,
                          bias_attr=False))
            layers.append(
                nn.InstanceNorm2D(curr_dim * 2,
                                  weight_attr=False,
                                  bias_attr=False))
            layers.append(nn.ReLU())
            curr_dim = curr_dim * 2

        # Bottleneck
        for i in range(repeat_num):
            layers.append(
                ResidualBlock(dim_in=curr_dim, dim_out=curr_dim, mode='t'))

        self.main = nn.Sequential(*layers)

    def forward(self, x):
        """forward"""
        out = self.main(x)
        return out


class GetMatrix(paddle.fluid.dygraph.Layer):
    def __init__(self, dim_in, dim_out):
        super(GetMatrix, self).__init__()
        self.get_gamma = nn.Conv2D(dim_in,
                                   dim_out,
                                   kernel_size=1,
                                   stride=1,
                                   padding=0,
                                   bias_attr=False)
        self.get_beta = nn.Conv2D(dim_in,
                                  dim_out,
                                  kernel_size=1,
                                  stride=1,
                                  padding=0,
                                  bias_attr=False)

    def forward(self, x):
        gamma = self.get_gamma(x)
        beta = self.get_beta(x)
        return gamma, beta


class MANet(paddle.nn.Layer):
    """MANet in PSGAN"""
    def __init__(self, conv_dim=64, repeat_num=3, w=0.01):
        super(MANet, self).__init__()
        self.encoder = TNetDown(conv_dim=conv_dim, repeat_num=repeat_num)
        curr_dim = conv_dim * 4
        self.w = w
        self.beta = nn.Conv2D(curr_dim, curr_dim, kernel_size=3, padding=1)
        self.gamma = nn.Conv2D(curr_dim, curr_dim, kernel_size=3, padding=1)
        self.simple_spade = GetMatrix(curr_dim, 1)  # get the makeup matrix
        self.repeat_num = repeat_num
        for i in range(repeat_num):
            setattr(self, "bottlenecks_" + str(i),
                    ResidualBlock(dim_in=curr_dim, dim_out=curr_dim, mode='t'))
        # Up-Sampling
        self.upsamplers = []
        self.up_betas = []
        self.up_gammas = []
        self.up_acts = []
        y_dim = curr_dim
        for i in range(2):
            layers = []
            layers.append(
                nn.Conv2DTranspose(curr_dim,
                                   curr_dim // 2,
                                   kernel_size=4,
                                   stride=2,
                                   padding=1,
                                   bias_attr=False))
            layers.append(
                nn.InstanceNorm2D(curr_dim // 2,
                                  weight_attr=False,
                                  bias_attr=False))

            setattr(self, "up_acts_" + str(i), nn.ReLU())
            setattr(
                self, "up_betas_" + str(i),
                nn.Conv2DTranspose(y_dim,
                                   curr_dim // 2,
                                   kernel_size=4,
                                   stride=2,
                                   padding=1))
            setattr(
                self, "up_gammas_" + str(i),
                nn.Conv2DTranspose(y_dim,
                                   curr_dim // 2,
                                   kernel_size=4,
                                   stride=2,
                                   padding=1))
            setattr(self, "up_samplers_" + str(i), nn.Sequential(*layers))
            curr_dim = curr_dim // 2
        self.img_reg = [
            nn.Conv2D(curr_dim,
                      3,
                      kernel_size=7,
                      stride=1,
                      padding=3,
                      bias_attr=False)
        ]
        self.img_reg = nn.Sequential(*self.img_reg)

    def forward(self, x, y, x_p, y_p, consistency_mask, mask_x, mask_y):
        """forward"""
        # y -> ref feature
        # x -> src img
        x = self.encoder(x)
        _, c, h, w = x.shape
        x_flat = x.reshape([-1, c, h * w])
        x_flat = self.w * x_flat
        if x_p is not None:
            x_flat = paddle.concat([x_flat, x_p], axis=1)

        _, c2, h2, w2 = y.shape
        y_flat = y.reshape([-1, c2, h2 * w2])
        y_flat = self.w * y_flat
        if y_p is not None:
            y_flat = paddle.concat([y_flat, y_p], axis=1)
        a_ = paddle.matmul(x_flat, y_flat, transpose_x=True) * 200.0

        # mask softmax
        if consistency_mask is not None:
            a_ = a_ - 100.0 * (1 - consistency_mask)
        a = F.softmax(a_, axis=-1)

        gamma, beta = self.simple_spade(y)

        beta = beta.reshape([-1, h2 * w2, 1])
        beta = paddle.matmul(a, beta)
        beta = beta.reshape([-1, 1, h2, w2])
        gamma = gamma.reshape([-1, h2 * w2, 1])
        gamma = paddle.matmul(a, gamma)
        gamma = gamma.reshape([-1, 1, h2, w2])
        x = x * (1 + gamma) + beta

        for i in range(self.repeat_num):
            layer = getattr(self, "bottlenecks_" + str(i))
            x = layer(x)

        for idx in range(2):
            layer = getattr(self, "up_samplers_" + str(idx))
            x = layer(x)
            layer = getattr(self, "up_acts_" + str(idx))
            x = layer(x)
        x = self.img_reg(x)
        x = paddle.tanh(x)
        return x, a


@GENERATORS.register()
class GeneratorPSGANAttention(paddle.nn.Layer):
    def __init__(self, conv_dim=64, repeat_num=3):
        super(GeneratorPSGANAttention, self).__init__()
        self.ma_net = MANet(conv_dim=conv_dim, repeat_num=repeat_num)
        self.md_net = MDNet(conv_dim=conv_dim, repeat_num=repeat_num)

    def forward(self, x, y, x_p, y_p, consistency_mask, mask_x, mask_y):
        """forward"""
        y = self.md_net(y)
        out, a = self.ma_net(x, y, x_p, y_p, consistency_mask, mask_x, mask_y)
        return out, a
