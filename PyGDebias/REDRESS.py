

import torch.nn as nn
import time
import numpy as np
import torch.nn.functional as F
import torch.optim as optim
from time import perf_counter
import torch
from torch.nn import Module
import math

import torch

from torch.nn.parameter import Parameter
from torch.nn.modules.module import Module
from torch.nn import Module




def accuracy(output, labels):
    preds = output.max(1)[1].type_as(labels)
    correct = preds.eq(labels).double()
    correct = correct.sum()
    return correct / len(labels)




class SGC(nn.Module):
    """
    A Simple PyTorch Implementation of Logistic Regression.
    Assuming the features have been preprocessed with k-step graph propagation.
    """
    def __init__(self, nfeat, nclass):
        super(SGC, self).__init__()

        self.W = nn.Linear(nfeat, nclass)
        self.W2 = nn.Linear(nfeat, nfeat)


    def forward(self, x):
        return self.W(x)

class GraphConvolution(Module):
    """
    Simple GCN layer, similar to https://arxiv.org/abs/1609.02907
    """

    def __init__(self, in_features, out_features, bias=True):
        super(GraphConvolution, self).__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.weight = Parameter(torch.FloatTensor(in_features, out_features))
        if bias:
            self.bias = Parameter(torch.FloatTensor(out_features))
        else:
            self.register_parameter('bias', None)
        self.reset_parameters()

    def reset_parameters(self):
        stdv = 1. / math.sqrt(self.weight.size(1))
        self.weight.data.uniform_(-stdv, stdv)
        if self.bias is not None:
            self.bias.data.uniform_(-stdv, stdv)

    def forward(self, input, adj):
        support = torch.mm(input, self.weight)
        output = torch.spmm(adj, support)
        if self.bias is not None:
            return output + self.bias
        else:
            return output

    def __repr__(self):
        return self.__class__.__name__ + ' (' \
               + str(self.in_features) + ' -> ' \
               + str(self.out_features) + ')'

class GCN(nn.Module):
    """
    A Two-layer GCN.
    """
    def __init__(self, nfeat, nhid, nclass, dropout):
        super(GCN, self).__init__()

        self.gc1 = GraphConvolution(nfeat, nhid)
        self.gc2 = GraphConvolution(nhid, nclass)
        self.dropout = dropout

    def forward(self, x, adj, use_relu=True):
        x = self.gc1(x, adj)
        if use_relu:
            x = F.relu(x)
        x = F.dropout(x, self.dropout, training=self.training)
        x = self.gc2(x, adj)
        return x



def get_model(model_opt, nfeat, nclass, nhid=0, dropout=0, cuda=True):
    if model_opt == "GCN":
        model = GCN(nfeat=nfeat,
                    nhid=nhid,
                    nclass=nclass,
                    dropout=dropout)
    elif model_opt == "SGC":
        model = SGC(nfeat=nfeat,
                    nclass=nclass)
    else:
        raise NotImplementedError('model:{} is not implemented!'.format(model_opt))

    if cuda: model.cuda()
    return model








def sgc_precompute(features, adj, degree):
    t = perf_counter()
    for i in range(degree):
        features = torch.spmm(adj, features)
    precompute_time = perf_counter()-t
    return features, precompute_time

def simi(output):  # new_version

    a = output.norm(dim=1)[:, None]
    the_ones = torch.ones_like(a)
    a = torch.where(a==0, the_ones, a)
    a_norm = output / a
    b_norm = output / a
    res = 5 * (torch.mm(a_norm, b_norm.transpose(0, 1)) + 1)

    return res


# by rows
def idcg_computation(x_sorted_scores, top_k):
    c = 2 * torch.ones_like(x_sorted_scores)[:top_k]
    numerator = c.pow(x_sorted_scores[:top_k]) - 1
    denominator = torch.log2(2 + torch.arange(x_sorted_scores[:top_k].shape[0], dtype=torch.float)).cuda()
    final = numerator / denominator

    return torch.sum(final)

# by rows
def dcg_computation(score_rank, top_k):
    c = 2 * torch.ones_like(score_rank)[:top_k]
    numerator = c.pow(score_rank[:top_k]) - 1
    denominator = torch.log2(2 + torch.arange(score_rank[:top_k].shape[0], dtype=torch.float))
    final = numerator / denominator

    return torch.sum(final)


def ndcg_exchange_abs(x_corresponding, j, k, idcg, top_k):
    new_score_rank = x_corresponding
    dcg1 = dcg_computation(new_score_rank, top_k)
    the_index = np.arange(new_score_rank.shape[0])
    temp = the_index[j]
    the_index[j] = the_index[k]
    the_index[k] = temp
    new_score_rank = new_score_rank[the_index]
    dcg2 = dcg_computation(new_score_rank, top_k)

    return torch.abs((dcg1 - dcg2) / idcg)


def err_computation(score_rank, top_k):
    the_maxs = torch.max(score_rank).repeat(1, score_rank.shape[0])
    c = 2 * torch.ones_like(score_rank)
    score_rank = (( c.pow(score_rank) - 1) / c.pow(the_maxs))[0]
    the_ones = torch.ones_like(score_rank)
    new_score_rank = torch.cat((the_ones, 1 - score_rank))

    for i in range(score_rank.shape[0] - 1):
        score_rank = torch.mul(score_rank, new_score_rank[-score_rank.shape[0] - 1 - i : -1 - i])
    the_range = torch.arange(0., score_rank.shape[0]) + 1

    final = (1 / the_range[0:]) * score_rank[0:]

    return torch.sum(final)



def err_exchange_abs(x_corresponding, j, k, top_k):
    new_score_rank = x_corresponding
    err1 = err_computation(new_score_rank, top_k)
    the_index = np.arange(new_score_rank.shape[0])
    temp = the_index[j]
    the_index[j] = the_index[k]
    the_index[k] = temp
    new_score_rank = new_score_rank[the_index]
    err2 = err_computation(new_score_rank, top_k)

    return torch.abs(err1 - err2)


def avg_err(x_corresponding, x_similarity, x_sorted_scores, y_ranks, top_k):
    the_maxs, _ = torch.max(x_corresponding, 1)
    the_maxs = the_maxs.reshape(the_maxs.shape[0], 1).repeat(1, x_corresponding.shape[1])
    c = 2 * torch.ones_like(x_corresponding)
    x_corresponding = ( c.pow(x_corresponding) - 1) / c.pow(the_maxs)
    the_ones = torch.ones_like(x_corresponding)
    new_x_corresponding = torch.cat((the_ones, 1 - x_corresponding), 1)

    for i in range(x_corresponding.shape[1] - 1):
        x_corresponding = torch.mul(x_corresponding, new_x_corresponding[:, -x_corresponding.shape[1] - 1 - i : -1 - i])
    the_range = torch.arange(0., x_corresponding.shape[1]).repeat(x_corresponding.shape[0], 1) + 1
    score_rank = (1 / the_range[:, 0:]) * x_corresponding[:, 0:]
    final = torch.mean(torch.sum(score_rank, axis=1))
    print("Now Average ERR@k = ", final.item())

    return final.item()



def avg_ndcg(x_corresponding, x_similarity, x_sorted_scores, y_ranks, top_k):

    c = 2 * torch.ones_like(x_sorted_scores[:, :top_k])

    numerator = c.pow(x_sorted_scores[:, :top_k]) - 1
    denominator = torch.log2(2 + torch.arange(x_sorted_scores[:, :top_k].shape[1], dtype=torch.float)).repeat(x_sorted_scores.shape[0], 1).cuda()
    idcg = torch.sum((numerator / denominator), 1)
    new_score_rank = torch.zeros(y_ranks.shape[0], y_ranks[:, :top_k].shape[1])
    numerator = c.pow(x_corresponding.cuda()[:, :top_k]) - 1
    denominator = torch.log2(2 + torch.arange(new_score_rank[:, :top_k].shape[1], dtype=torch.float)).repeat(x_sorted_scores.shape[0], 1).cuda()
    ndcg_list = torch.sum((numerator / denominator), 1) / idcg
    avg_ndcg = torch.mean(ndcg_list)
    print("Now Average NDCG@k = ", avg_ndcg.item())

    return avg_ndcg.item()



def lambdas_computation(x_similarity, y_similarity, top_k, k_para, sigma_1):
    max_num = 2000000
    x_similarity[range(x_similarity.shape[0]), range(x_similarity.shape[0])] = max_num * torch.ones_like(x_similarity[0, :])
    y_similarity[range(y_similarity.shape[0]), range(y_similarity.shape[0])] = max_num * torch.ones_like(y_similarity[0, :])

    # ***************************** ranking ******************************
    (x_sorted_scores, x_sorted_idxs) = x_similarity.sort(dim=1, descending=True)
    (y_sorted_scores, y_sorted_idxs) = y_similarity.sort(dim=1, descending=True)
    y_ranks = torch.zeros(y_similarity.shape[0], y_similarity.shape[0])
    the_row = torch.arange(y_similarity.shape[0]).view(y_similarity.shape[0], 1).repeat(1, y_similarity.shape[0])
    y_ranks[the_row, y_sorted_idxs] = 1 + torch.arange(y_similarity.shape[1]).repeat(y_similarity.shape[0], 1).float()

    # ***************************** pairwise delta ******************************
    sigma_tuned = sigma_1
    length_of_k = k_para * top_k
    y_sorted_scores = y_sorted_scores[:, 1 :(length_of_k + 1)]
    y_sorted_idxs = y_sorted_idxs[:, 1 :(length_of_k + 1)]
    x_sorted_scores = x_sorted_scores[:, 1 :(length_of_k + 1)]
    pairs_delta = torch.zeros(y_sorted_scores.shape[1], y_sorted_scores.shape[1], y_sorted_scores.shape[0])

    for i in range(y_sorted_scores.shape[0]):
        pairs_delta[:, :, i] = y_sorted_scores[i, :].view(y_sorted_scores.shape[1], 1) - y_sorted_scores[i, :].float()

    fraction_1 = - sigma_tuned / (1 + (pairs_delta * sigma_tuned).exp())
    x_delta = torch.zeros(y_sorted_scores.shape[1], y_sorted_scores.shape[1], y_sorted_scores.shape[0])
    x_corresponding = torch.zeros(x_similarity.shape[0], length_of_k)

    for i in range(x_corresponding.shape[0]):
        x_corresponding[i, :] = x_similarity[i, y_sorted_idxs[i, :]]

    for i in range(x_corresponding.shape[0]):
        # print(i / x_corresponding.shape[0])
        x_delta[:, :, i] = x_corresponding[i, :].view(x_corresponding.shape[1], 1) - x_corresponding[i, :].float()

    S_x = torch.sign(x_delta)
    zero = torch.zeros_like(S_x)
    S_x = torch.where(S_x < 0, zero, S_x)

    # ***************************** NDCG delta from ranking ******************************

    ndcg_delta = torch.zeros(x_corresponding.shape[1], x_corresponding.shape[1], x_corresponding.shape[0])
    for i in range(y_similarity.shape[0]):
        if i >= 0.6 * y_similarity.shape[0]:
            break
        idcg = idcg_computation(x_sorted_scores[i, :], top_k)
        for j in range(x_corresponding.shape[1]):
            for k in range(x_corresponding.shape[1]):
                if S_x[j, k, i] == 0:
                    continue
                if j < k:
                    the_delta = ndcg_exchange_abs(x_corresponding[i, :], j, k, idcg, top_k)
                    # print(the_delta)
                    ndcg_delta[j, k, i] = the_delta
                    ndcg_delta[k, j, i] = the_delta

    without_zero = S_x * fraction_1 * ndcg_delta
    lambdas = torch.zeros(x_corresponding.shape[0], x_corresponding.shape[1])
    for i in range(lambdas.shape[0]):
        for j in range(lambdas.shape[1]):
            lambdas[i, j] = torch.sum(without_zero[j, :, i]) - torch.sum(without_zero[:, j, i])   # 本来是 -

    mid = torch.zeros_like(x_similarity)
    the_x = torch.arange(x_similarity.shape[0]).repeat(length_of_k, 1).transpose(0, 1).reshape(length_of_k * x_similarity.shape[0], 1).squeeze()
    the_y = y_sorted_idxs.reshape(length_of_k * x_similarity.shape[0], 1).squeeze()
    the_data = lambdas.reshape(length_of_k * x_similarity.shape[0], 1).squeeze()
    mid.index_put_((the_x, the_y.long()), the_data.cuda())

    return mid, x_sorted_scores, y_sorted_idxs, x_corresponding


def lambdas_computation_only_review(x_similarity, y_similarity, top_k, k_para):
    max_num = 2000000
    x_similarity[range(x_similarity.shape[0]), range(x_similarity.shape[0])] = max_num * torch.ones_like(x_similarity[0, :])
    y_similarity[range(y_similarity.shape[0]), range(y_similarity.shape[0])] = max_num * torch.ones_like(y_similarity[0, :])

    # ***************************** ranking ******************************
    (x_sorted_scores, x_sorted_idxs) = x_similarity.sort(dim=1, descending=True)
    (y_sorted_scores, y_sorted_idxs) = y_similarity.sort(dim=1, descending=True)
    y_ranks = torch.zeros(y_similarity.shape[0], y_similarity.shape[0])
    the_row = torch.arange(y_similarity.shape[0]).view(y_similarity.shape[0], 1).repeat(1, y_similarity.shape[0])
    y_ranks[the_row, y_sorted_idxs] = 1 + torch.arange(y_similarity.shape[1]).repeat(y_similarity.shape[0], 1).float()
    length_of_k = k_para * top_k - 1
    y_sorted_idxs = y_sorted_idxs[:, 1 :(length_of_k + 1)]
    x_sorted_scores = x_sorted_scores[:, 1 :(length_of_k + 1)]
    x_corresponding = torch.zeros(x_similarity.shape[0], length_of_k)

    for i in range(x_corresponding.shape[0]):
        x_corresponding[i, :] = x_similarity[i, y_sorted_idxs[i, :]]

    return x_sorted_scores, y_sorted_idxs, x_corresponding



class SGC(nn.Module):
    """
    A Simple PyTorch Implementation of Logistic Regression.
    Assuming the features have been preprocessed with k-step graph propagation.
    """
    def __init__(self, nfeat, nclass):
        super(SGC, self).__init__()

        self.W = nn.Linear(nfeat, nclass)
        self.W2 = nn.Linear(nfeat, nfeat)


    def forward(self, x):
        return self.W(x)

class GraphConvolution(Module):
    """
    Simple GCN layer, similar to https://arxiv.org/abs/1609.02907
    """

    def __init__(self, in_features, out_features, bias=True):
        super(GraphConvolution, self).__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.weight = Parameter(torch.FloatTensor(in_features, out_features))
        if bias:
            self.bias = Parameter(torch.FloatTensor(out_features))
        else:
            self.register_parameter('bias', None)
        self.reset_parameters()

    def reset_parameters(self):
        stdv = 1. / math.sqrt(self.weight.size(1))
        self.weight.data.uniform_(-stdv, stdv)
        if self.bias is not None:
            self.bias.data.uniform_(-stdv, stdv)

    def forward(self, input, adj):
        support = torch.mm(input, self.weight)
        output = torch.spmm(adj, support)
        if self.bias is not None:
            return output + self.bias
        else:
            return output

    def __repr__(self):
        return self.__class__.__name__ + ' (' \
               + str(self.in_features) + ' -> ' \
               + str(self.out_features) + ')'

class GCN(nn.Module):
    """
    A Two-layer GCN.
    """
    def __init__(self, nfeat, nhid, nclass, dropout):
        super(GCN, self).__init__()

        self.gc1 = GraphConvolution(nfeat, nhid)
        self.gc2 = GraphConvolution(nhid, nclass)
        self.dropout = dropout

    def forward(self, x, adj, use_relu=True):
        x = self.gc1(x, adj)
        if use_relu:
            x = F.relu(x)
        x = F.dropout(x, self.dropout, training=self.training)
        x = self.gc2(x, adj)
        return x



def get_model(model_opt, nfeat, nclass, nhid=0, dropout=0, cuda=True):
    if model_opt == "GCN":
        model = GCN(nfeat=nfeat,
                    nhid=nhid,
                    nclass=nclass,
                    dropout=dropout)
    elif model_opt == "SGC":
        model = SGC(nfeat=nfeat,
                    nclass=nclass)
    else:
        raise NotImplementedError('model:{} is not implemented!'.format(model_opt))

    if cuda: model.cuda()
    return model



class REDRESS(nn.Module):
    def __init__(self, adj, features, labels, idx_train, idx_val, idx_test, lr=0.003, hidden=16, dropout=0.6, weight_decay=5e-4, degree=2, model_name="GCN", top_k=10, sigma_1=2e-2, cuda=1, pre_train=1500, epochs=20):
        super(REDRESS, self).__init__()

        self.model_name = model_name

        self.model = get_model(model_name, features.size(1), 2, hidden, dropout,
                          cuda)  # labels.max().item() + 1

        self.optimizer = optim.Adam(self.model.parameters(), lr=lr,
                               weight_decay=weight_decay)
        # self.optimizer = optim.RMSprop(self.model.parameters(), lr=lr,
        #                        weight_decay=weight_decay)
                            

        self.all_ndcg_list_test = []
        self.lambdas_para = 1
        self.k_para = 1
        self.sigma_1 = sigma_1
        self.top_k = top_k
        self.pre_train = pre_train
        self.epochs = epochs


        self.features = features
        self.adj = adj
        self.labels = labels
        self.idx_train = idx_train
        self.idx_val = idx_val
        self.idx_test = idx_test

        if model_name == "SGC":
            self.features, self.precompute_time = sgc_precompute(features, adj, degree)


        if cuda:
            self.model.cuda()
            self.features = features.cuda()
            self.adj = adj.cuda()
            self.labels = labels.cuda()
            self.idx_train = idx_train.cuda()
            self.idx_val = idx_val.cuda()
            self.idx_test = idx_test.cuda()



    def train(self, epoch, model_name, flag=0):
        t = time.time()
        self.model.train()
        self.optimizer.zero_grad()
        if model_name == 'SGC':
            output1 = self.model(self.features)
        else:
            output1 = self.model(self.features, self.adj)

        # print(output1[self.idx_train])
        # print(self.labels[self.idx_train])
        the_softmax = torch.nn.Softmax(dim=1)


        loss_train = F.cross_entropy(the_softmax(output1[self.idx_train]), self.labels[self.idx_train])
        acc_train = 0  # accuracy(output1[self.idx_train], self.labels[self.idx_train])
        if flag == 0:
            loss_train.backward(retain_graph=True)
        else:
            loss_train.backward()
            self.optimizer.step()

        self.model.eval()
        if model_name == 'SGC':
            output = self.model(self.features)
        else:
            output = self.model(self.features, self.adj)


        loss_val = F.cross_entropy(the_softmax(output[self.idx_val]), self.labels[self.idx_val])
        acc_val =  0  # accuracy(output[self.idx_val], self.labels[self.idx_val])
        if epoch%300==0:
            print('Epoch: {:04d}'.format(epoch+1),
                  'loss_train: {:.4f}'.format(loss_train.item()),
                #   'acc_train: {:.4f}'.format(acc_train.item()),
                  'loss_val: {:.4f}'.format(loss_val.item()),
                #   'acc_val: {:.4f}'.format(acc_val.item()),
                  'time: {:.4f}s'.format(time.time() - t))

        return output1

    def train_fair(self, epoch, model_name, adj, output):
        self.model.train()
        y_similarity1 = simi(output[self.idx_train])
        x_similarity = simi(self.features[self.idx_train])
        lambdas1, x_sorted_scores, y_sorted_idxs, x_corresponding = lambdas_computation(x_similarity, y_similarity1, self.top_k, self.k_para, self.sigma_1)
        assert lambdas1.shape == y_similarity1.shape

        y_similarity = simi(output[self.idx_test])
        x_similarity = simi(self.features[self.idx_test])

        print("Ranking optimizing... ")
        x_sorted_scores, y_sorted_idxs, x_corresponding = lambdas_computation_only_review(x_similarity, y_similarity, self.top_k, self.k_para)
        self.all_ndcg_list_test.append(avg_ndcg(x_corresponding, x_similarity, x_sorted_scores, y_sorted_idxs, self.top_k))

        y_similarity1.backward(self.lambdas_para * lambdas1)
        self.optimizer.step()




    def fit(self, model_name='GCN'):

        for epoch in range(self.pre_train):
            output = self.train(epoch, model_name, 1)

        for epoch in range(self.epochs):
            output = self.train(epoch, model_name)
            self.train_fair(epoch, model_name, self.adj, output)





    def predict(self):

        self.model.eval()
        if self.model_name == 'SGC':
            output = self.model(self.features)
        else:
            output = self.model(self.features, self.adj)

        loss_test = F.cross_entropy(output[self.idx_test], self.labels[self.idx_test])
        acc_test =  0  # accuracy(output[self.idx_test], self.labels[self.idx_test])
        print("Test set results:",
              "loss= {:.4f}".format(loss_test.item()) #,
            #   "accuracy= {:.4f}".format(acc_test.item())
              )

        return output, self.labels, self.idx_test



