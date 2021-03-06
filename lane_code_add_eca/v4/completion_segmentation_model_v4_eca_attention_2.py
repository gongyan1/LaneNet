# -*- coding: utf-8 -*-
"""
Created on Sat Mar 28 10:57:24 2020

@author: kerui
"""

import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision.models import resnet
import cv2

print_model = False # 是否打印网络结构

# 初始化参数
def init_weights(m):
    if isinstance(m, nn.Conv2d) or isinstance(m, nn.Linear):
        m.weight.data.normal_(0, 1e-3)
        if m.bias is not None:
            m.bias.data.zero_()
    elif isinstance(m, nn.ConvTranspose2d):
        m.weight.data.normal_(0, 1e-3)
        if m.bias is not None:
            m.bias.data.zero_()
    elif isinstance(m, nn.BatchNorm2d):
        m.weight.data.fill_(1)
        m.bias.data.zero_()

# 卷积-> 批标准化-> relu
def conv_bn_relu(in_channels, out_channels, kernel_size, \
        stride=1, padding=0, bn=True, relu=True):
    bias = not bn
    layers = []
    layers.append(
        nn.Conv2d(in_channels,
                  out_channels,
                  kernel_size,
                  stride,
                  padding,
                  bias=bias))
    if bn:
        layers.append(nn.BatchNorm2d(out_channels))
    if relu:
        layers.append(nn.LeakyReLU(0.2, inplace=True))
    layers = nn.Sequential(*layers)

    # initialize the weights
    for m in layers.modules():
        init_weights(m)

    return layers

# 转置卷积-> 批标准化-> relu
def convt_bn_relu(in_channels, out_channels, kernel_size, \
        stride=1, padding=0, output_padding=0, bn=True, relu=True):
    bias = not bn
    layers = []
    layers.append(
        nn.ConvTranspose2d(in_channels,
                           out_channels,
                           kernel_size,
                           stride,
                           padding,
                           output_padding,
                           bias=bias))
    if bn:
        layers.append(nn.BatchNorm2d(out_channels))
    if relu:
        layers.append(nn.LeakyReLU(0.2, inplace=True))
    layers = nn.Sequential(*layers)

    # initialize the weights
    for m in layers.modules():
        init_weights(m)

    return layers
                    
'''
前融合：在第一层ResBlock前融合
    

'''     
# 点云+RGB作为输入，只有车道线分割这一分支      
class DepthCompletionFrontNet(nn.Module):
    def __init__(self, args):
        assert (
            args.layers in [18, 34, 50, 101, 152]
        ), 'Only layers 18, 34, 50, 101, and 152 are defined, but got {}'.format(
            args.layers)
        super(DepthCompletionFrontNet, self).__init__()
        self.modality = args.input

        # 点云:原始 + KNN补全 + 高度
        if 'd' in self.modality:
            #channels = 64 * 3 // len(self.modality)
            channels = 64
            self.conv1_d = conv_bn_relu(3,
                                        channels,
                                        kernel_size=3,
                                        stride=1,
                                        padding=1)
            
        # rgb
        if 'rgb' in self.modality:
            #channels = 64 * 3 // len(self.modality)
            channels = 64
            self.conv1_img = conv_bn_relu(3,
                                          channels,
                                          kernel_size=3,
                                          stride=1,
                                          padding=1)
            
        # gray
        elif 'g' in self.modality:
            channels = 64 // len(self.modality)
            self.conv1_img = conv_bn_relu(1,
                                          channels,
                                          kernel_size=3,
                                          stride=1,
                                          padding=1)

        # 加载resnet预训练模型
        pretrained_model = resnet.__dict__['resnet{}'.format(
            args.layers)](pretrained=args.pretrained)
        if not args.pretrained:
            pretrained_model.apply(init_weights)
            
        # encoding layers
        
        # 分支1
        # resnet预训练模型的第一个块
        self.conv2 = conv_bn_relu(in_channels=64,
                                         out_channels=64,
                                         kernel_size=3,
                                         stride=1,
                                         padding=1)
   
        # resnet预训练模型的第二个块
        self.conv3 = conv_bn_relu(in_channels=64,
                                         out_channels=128,
                                         kernel_size=3,
                                         stride=2,
                                         padding=1)
        
        # 分支2
        # resnet预训练模型的第一个块
        self.conv2_ = pretrained_model._modules['layer1']
        # resnet预训练模型的第二个块
        self.conv3_ = pretrained_model._modules['layer2']
        
        # resnet预训练模型的第三个块

        self.conv4 = conv_bn_relu(in_channels=256,
                                         out_channels=256,
                                         kernel_size=3,
                                         stride=2,
                                         padding=1)
        # resnet预训练模型的第四个块
        del pretrained_model  # clear memory
            
        num_channels = 256
        '''
        self.conv5 = conv_bn_relu(num_channels,
                                  256,
                                  kernel_size=3,
                                  stride=2,
                                  padding=1)
        '''
        # 两个分支共用的两层解码层
        kernel_size = 3
        stride = 2
        '''
        self.convt4 = convt_bn_relu(in_channels=256,
                                    out_channels=128,
                                    kernel_size=kernel_size,
                                    stride=stride,
                                    padding=1,
                                    output_padding=1)
        '''
        self.conv_connnet = conv_bn_relu(in_channels=256,
                                         out_channels=128,
                                         kernel_size=1,
                                         stride=1,
                                         padding=0)
        self.convt3 = convt_bn_relu(in_channels=(256 + 128),
                                    out_channels=64,
                                    kernel_size=kernel_size,
                                    stride=stride,
                                    padding=1,
                                    output_padding=1)
        
        # decoding layers for lane segmentation
        self.convt2_ = convt_bn_relu(in_channels=(128 + 128 + 64),
                                    out_channels=64,
                                    kernel_size=kernel_size,
                                    stride=stride,
                                    padding=1,
                                    output_padding=1)
        self.convt1_ = convt_bn_relu(in_channels=64 + 64 + 64,
                                    out_channels=64,
                                    kernel_size=kernel_size,
                                    stride=1,
                                    padding=1)
        self.convtf_ = conv_bn_relu(in_channels=64 + 64 + 64,
                                   out_channels=2, # 二分类
                                   kernel_size=1,
                                   stride=1,
                                   bn=False,
                                   relu=False)
        self.softmax_lane = self.softmax = nn.LogSoftmax(dim=1)
        # sk_attention-0512 -第一、二层
        planes = 128
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.conv_fc1 = nn.Conv2d(planes, planes//2, 1, bias=False)
        self.conv_fc2 = nn.Conv2d(planes//2, planes, 1, bias=False)
        self.D1 = planes//2
        # sk_attention-0512 -第三层
        planes2 = 256
        self.conv_fc13 = nn.Conv2d(planes2, planes2//2, 1, bias=False)
        self.conv_fc23 = nn.Conv2d(planes2//2, planes2, 1, bias=False)
        self.D3 = planes2//2

    def make_resnet_layer(self, block, planes, blocks, stride=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                nn.Conv2d(self.inplanes, planes * block.expansion,
                          kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm2d(planes * block.expansion),
            )

        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample))
        self.inplanes = planes * block.expansion
        for i in range(1, blocks):
            layers.append(block(self.inplanes, planes))

        return nn.Sequential(*layers)


    def eca_layer(self, x, gamma=2, b=1):
        # eca-net
        # 原理：通过GPA（全局平均池化）转为1*1*C的向量，再通过1维conv进行权重更新

        N, C, H, W = x.size()
        t = int(abs((math.log(C, 2) + b) / gamma))
        k_size = t if t % 2 else t + 1
        # k_size = 3
        avg_pool_eca = nn.AdaptiveAvgPool2d(1)
        # conv1d_eca = nn.Conv1d(1, 1, kernel_size=k_size, padding=(k_size - 1) // 2, bias=False)
        conv1d_eca = nn.Conv1d(1, 1, kernel_size=k_size, padding=int(k_size // 2), bias=False)
        sigmoid_eca = nn.Sigmoid()

        y = avg_pool_eca(x)
        # print(x)
        # print(y)
        y = y.cpu()
        y = conv1d_eca(y.squeeze(-1).transpose(-1, -2))
        # print("dasdasada")
        y = y.transpose(-1, -2).unsqueeze(-1)
        y = sigmoid_eca(y)
        y = y.cuda()

        # 将 y 变成和 x 一样的 shape
        return x * y.expand_as(x)





    def forward(self, x):
        if print_model:
            print("\n-------------------------encoder-------------------------\n")
        # first layer
        if 'd' in self.modality:
            if print_model:
                print("\n    input shape of reflectance: {}".format(x['d'].shape))
            conv1_d = self.conv1_d(x['pc'])
            if print_model:
                print("\n    first layer 3x3 conv_bn_relu for reflectance --> output shape: {}".format(conv1_d.shape))
        if 'rgb' in self.modality:
            if print_model:
                print("\n    input shape of rgb: {}".format(x['rgb'].shape))
            conv1_img = self.conv1_img(x['rgb'])
            if print_model:
                print("\n    first layer 3x3 conv_bn_relu for rgb --> output shape: {}".format(conv1_img.shape))
        elif 'g' in self.modality:
            if print_model:
                print("\n    input shape of gray image: {}".format(x['g'].shape))
            conv1_img = self.conv1_img(x['g'])
            if print_model:
                print("\n    first layer 3x3 conv_bn_relu for Gray Image --> output shape: {}".format(conv1_img.shape))
        # else:
        # 第一层注意力机制 -sk -0513
        # d1 = torch.cat((conv1_d, conv1_img), 1)
        # d = self.avg_pool(d1)
        # d = self.conv_fc1(d)
        # # d = self.bn_fc1(d)
        # d = F.relu(d)
        # d = self.conv_fc2(d)
        # d = torch.unsqueeze(d, 1).view(-1, 2, self.D1, 1, 1)
        # d = F.softmax(d, 1)
        # # 0 1 如果交换会不会有效果
        # conv1_d = conv1_d * d[:, 0, :, :, :].squeeze(1)
        # conv1_img = conv1_img * d[:, 1, :, :, :].squeeze(1)
        #
        # conv1_cat = torch.cat((conv1_d, conv1_img), 1)



        #eca-net
        d1 = torch.cat((conv1_d, conv1_img), 1)
        conv1_cat = self.eca_layer(d1)




        # encoder
        # 第二层
        # 分支1
        conv2 = self.conv2(conv1_d)
        if print_model:
            print("\n    ResNet Block{} output shape: {}".format(1, conv2.shape))
        # 分支2
        conv2_ = self.conv2_(conv1_img)
        if print_model:
            print("\n    ResNet Block{} output shape: {}".format(1, conv2.shape))


        # D2 = torch.cat((conv2, conv2_), 1)
        # d2 = self.avg_pool(D2)
        # d2 = self.conv_fc1(d2)
        # # d = self.bn_fc1(d)
        # d2 = F.relu(d2)
        # d2 = self.conv_fc2(d2)
        # d2 = torch.unsqueeze(d2, 1).view(-1, 2, self.D1, 1, 1)
        # d2 = F.softmax(d2, 1)
        # # 0 1 如果交换会不会有效果
        # conv2 = conv2 * d2[:, 0, :, :, :].squeeze(1)
        # conv2_ = conv2_ * d2[:, 1, :, :, :].squeeze(1)
        #
        # conv2_cat = torch.cat((conv2_, conv2), 1)



        # eca-net

        D2 = torch.cat((conv2, conv2_), 1)
        conv2_cat = self.eca_layer(D2)



        conv3 = self.conv3(conv2)  # batchsize * ? * 176 * 608
        if print_model:
            print("\n    ResNet Block{} output shape: {}".format(2, conv3.shape))
        
        conv3_ = self.conv3_(conv2_)  # batchsize * ? * 176 * 608
        if print_model:
            print("\n    ResNet Block{} output shape: {}".format(2, conv3.shape))
        # 第三层注意力机制
        # D3 = torch.cat((conv3, conv3_), 1)
        # d3 = self.avg_pool(D3)
        # d3 = self.conv_fc13(d3)
        # # d = self.bn_fc1(d)
        # d3 = F.relu(d3)
        # d3 = self.conv_fc23(d3)
        # d3 = torch.unsqueeze(d3, 1).view(-1, 2, self.D3, 1, 1)
        # d3 = F.softmax(d3, 1)
        # # 0 1 如果交换会不会有效果
        # conv3 = conv3 * d3[:, 0, :, :, :].squeeze(1)
        # conv3_ = conv3_ * d3[:, 1, :, :, :].squeeze(1)
        # conv3 = torch.cat((conv3, conv3_), 1)



        # eca-net
        D3 = torch.cat((conv3, conv3_), 1)
        conv3 = self.eca_layer(D3)




        conv4 = self.conv4(conv3)  # batchsize * ? * 88 * 304
        if print_model:
            print("\n    ResNet Block{} output shape: {}".format(3, conv4.shape))

        if print_model:
            print("\n-------------------------decoder for reflectance completion-------------------------\n")
            
        # 两个分支共用的两层解码层
        convt4 = self.conv_connnet(conv4)
        y_common = torch.cat((convt4, conv4), 1)
        if print_model:
            print("\n    skip connection from ResNet Block{}".format(3))

        convt3 = self.convt3(y_common)
        if print_model:
            print("\n    3x3 TransposeConv_bn_relu {} --> output shape: {}".format(3, convt3.shape))
        y_common = torch.cat((convt3, conv3), 1)
        if print_model:
            print("\n    skip connection from ResNet Block{}".format(2))

        # decoder for lane segmentation
        convt2_ = self.convt2_(y_common)
        if print_model:
            print("\n    3x3 TransposeConv_bn_relu {} --> output shape: {}".format(4, convt2_.shape))
        y_ = torch.cat((convt2_, conv2_cat), 1)
        if print_model: 
            print("\n    skip connection from ResNet Block{}".format(1))
        
        convt1_ = self.convt1_(y_)
        if print_model:
            print("\n    3x3 TransposeConv_bn_relu {} --> output shape: {}".format(5, convt1_.shape))
        y_ = torch.cat((convt1_, conv1_cat), 1)
        if print_model:
            print("\n    skip connection from the concat feature of first layer")
        
        y_ = self.convtf_(y_)
        if print_model:
            print("\n    the end layer 1x1 conv_bn_relu --> output shape: {}".format(y_.shape))
        
        lane = self.softmax_lane(y_)
        if print_model:
            print("\n    softmax for road segmentation --> output shape: {}".format(lane.shape))
        return lane

