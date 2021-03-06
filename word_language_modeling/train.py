from __future__ import print_function
import numpy as np
import theano
import theano.tensor as T
import os
import time
import gzip
import lasagne
import cPickle
import sys
sys.path.append('../')
import prepare_data
import networks
import argparse
import sys
import codecs
from lasagne.regularization import regularize_layer_params, l2, l1

np.random.seed(2345)

#  SETTINGS
parser = argparse.ArgumentParser(description='Train word-level language model with LSTM/QRNN.')

# word level specs
parser.add_argument("--model_seq_len", type=int, default=105, help="How many steps to unroll")
parser.add_argument("--init_W", type=str, default="lasagne.init.Normal(0.1)", help="Initial parameter values")
parser.add_argument("--init_b", type=str, default="lasagne.init.Constant(0)", help="Initial parameter values")
parser.add_argument("--input_act", type=str, default="lasagne.nonlinearities.tanh", help="Activations of RNN")
parser.add_argument("--gate_act", type=str, default="lasagne.nonlinearities.sigmoid", help="Gate activations of RNN")
parser.add_argument("--rec_num_units", type=int, default=640, help="Number of hidden units")
parser.add_argument("--embedding_size", type=int, default=640, help="Embedding size")
parser.add_argument("--dropout_frac", type=float, default=0.5, help="optional recurrent dropout")
parser.add_argument("--peepholes", type=int, default=0, help="Peephole connections in LSTM")
parser.add_argument("--untie_biases", type=int, default=1, help="Biases of QRNN")
parser.add_argument("--number_of_rnn_layers", type=int, default=2, help="How many RNNs will be stacked")
parser.add_argument("--k", type=int, default=[2, 2], nargs="+", help="Filter size in convolution")
parser.add_argument("--pooling", type=str, default="fo", help="f, fo")
parser.add_argument("--batch_norm", type=int, default=0, help="Batch norm")
parser.add_argument("--rnn_type", type=str, default="drelu", help="Type of model to train: lstm,qrnn,drelu,delu")
parser.add_argument("--elu_alpha", type=float, default=0.1, help="Elu alpha value")

# training
parser.add_argument("--batch_size", type=int, default=20, help="Batch size")
parser.add_argument("--lr", type=float, default=1, help="Learning rate")
parser.add_argument("--optimizer", type=str, default="lasagne.updates.sgd", help="Optimizer function: sgd, adam...")
parser.add_argument("--decay", type=float, default=0.95, help="Decay factor")
parser.add_argument("--no_decay_epochs", type=float, default=6, help="Run this many epochs before first decay")
parser.add_argument("--max_grad_norm", type=float, default=10, help="Scale steps if norm is above this value")
parser.add_argument("--grad_clip", type=float, default=0, help="Grad clipping value")
parser.add_argument("--num_epochs", type=int, default=100, help="Number of epochs to run")
parser.add_argument("--tol", type=float, default=1e-6, help="Numerical stability")
parser.add_argument("--L2_reg", type=float, default=0, help="L2 regularization")
parser.add_argument("--L1_reg", type=float, default=0, help="L1 regularization")
# data
parser.add_argument("--save_file", type=str, default="lm",help="Prefix for models")
parser.add_argument("--save_dir", type=str, default="../models/word_language_modeling/",help="Folder for storing all models")
args = parser.parse_args()
paras = vars(args)

if len(paras["k"]) != 1:
    if len(paras["k"]) != paras["number_of_rnn_layers"]:
        raise Exception("k paras do not equal number of rnn layers")
else:
    paras["k"] = paras["k"] * paras["number_of_rnn_layers"]

# load data
train, valid, test, vocab_map, vocab_idx = prepare_data.getdata()

# pad data
if paras["rnn_type"] == "lstm":
    context = 1
else:
    context = paras["k"][0]
x_train, y_train = prepare_data.reorder(train, paras["batch_size"], paras["model_seq_len"], context=context - 1)
x_valid, y_valid, mask_valid = prepare_data.reorder(valid, paras["batch_size"], paras["model_seq_len"], padding=True,
                                                    context=context - 1)
x_test, y_test, mask_test = prepare_data.reorder(test, paras["batch_size"], paras["model_seq_len"], padding=True,
                                                 context=context - 1)

paras["vocab_size"] = vocab_idx[0]

print("-" * 80)
print("Vocab size: ", (paras["vocab_size"]))
print("Data shapes")
print("Train data: ", (x_train.shape))
print("Valid data: ", (x_valid.shape))
print("Test data : ", (x_test.shape))
print("-" * 80)

# Theano symbolic vars
sym_x = T.imatrix()
sym_y = T.imatrix()

hids = []

for _ in range(paras["number_of_rnn_layers"]):
    if paras["rnn_type"] == "lstm":
        hids.extend([T.matrix(), T.matrix()])
    else:
        hids.extend([T.tensor3(), T.matrix()])

sh_lr = theano.shared(lasagne.utils.floatX(paras["lr"]))

l_out, l_hids = networks.build_network(paras, sym_x, hids)

print("Number of Params: " + str(lasagne.layers.count_params(l_out)))

all_out = [l_out]
all_out.extend(l_hids)

train_out = lasagne.layers.get_output(
    all_out, deterministic=False)
hids_out_train = train_out[1:]
train_out = train_out[0]

eval_out = lasagne.layers.get_output(
    all_out, deterministic=True)
hids_out_eval = eval_out[1:]
eval_out = eval_out[0]

cost_train = T.mean(networks.calc_cross_ent(train_out, sym_y, paras))
if paras["L2_reg"] > 0:
    cost_train += paras["L2_reg"] * regularize_layer_params(l_out, l2)
if paras["L1_reg"] > 0:
    cost_train += paras["L1_reg"] * regularize_layer_params(l_out, l1)
cost_eval = networks.calc_cross_ent(eval_out, sym_y, paras)

all_params = lasagne.layers.get_all_params(l_out, trainable=True)

updates, norm = networks.gradient_updates(cost_train, all_params, paras, sh_lr,
                                          update_function=eval(paras["optimizer"]))

print("compiling f_eval...")
fun_inp = [sym_x, sym_y]

if paras["rnn_type"] != "lstm":
    hids.pop(-2)

fun_inp.extend(hids)
outs = [cost_eval]
outs.extend(hids_out_eval)

f_eval = theano.function(fun_inp, outs)

print("compiling f_train...")

outs = [cost_train, norm]
outs.extend(hids_out_train)

f_train = theano.function(fun_inp, outs, updates=updates)

print("Store settings")
start_time_str = time.strftime("%d_%b_%Y_%H_%M_%S")
save_file_model = paras["save_file"] + "_data_" + start_time_str
save_file_settings = paras["save_file"] + "_settings_" + start_time_str
file = codecs.open(os.path.join(paras["save_dir"], save_file_settings), "w")
file.write(str(paras) + "\n")
file.close()

n_batches_train = x_train.shape[0] / paras["batch_size"]
print("started training")
last_perplexity = sys.maxint
best_valid_perplexity = sys.maxint
corresponding_train_perplexity = sys.maxint
for epoch in range(paras["num_epochs"]):

    # prepare hidden states that will be passed
    l_cost, l_norm, batch_time = [], [], time.time()
    hids = []

    if paras["rnn_type"] == "lstm":
        for i in range(paras["number_of_rnn_layers"]):
            hids.append(np.zeros((paras["batch_size"], paras["rec_num_units"]),
                                 dtype=np.float32))
            hids.append(np.zeros((paras["batch_size"], paras["rec_num_units"]),
                                 dtype=np.float32))
    else:
        for i in range(paras["number_of_rnn_layers"] - 1):
            input_size = paras["rec_num_units"]
            hids.extend([np.zeros((paras["batch_size"], paras["k"][(i + 1) % len(paras["k"])] - 1, input_size),
                                  dtype=np.float32), np.zeros((paras["batch_size"], paras["rec_num_units"]),
                                                              dtype=np.float32)])
        hids.append(np.zeros((paras["batch_size"], paras["rec_num_units"]),
                             dtype=np.float32))

    # it's a hacky way of introducing variable length arguments and being theano compatible
    # feel free to suggest a pull request with better code
    function_str = "f_train("
    for j in range(2 + len(hids)):
        function_str += "input[" + str(j) + "],"
    function_str = function_str[:-1] + ")"

    for i in range(n_batches_train):

        x_batch = x_train[i * paras["batch_size"]:(i + 1) * paras["batch_size"]]  # single batch
        y_batch = y_train[i * paras["batch_size"]:(i + 1) * paras["batch_size"]]
        input = [x_batch, y_batch]
        input.extend(hids)
        all = eval(function_str)
        cost = all[0]
        norm = all[1]
        hids = all[2: 2 + len(hids)]

        l_cost.append(cost)
        l_norm.append(norm)

        if i % 100 == 0 and i > 0:
            print("Iteration: %s, perplexity %s" % (i, np.exp(np.sum(l_cost) / len(l_cost))))

    elapsed = time.time() - batch_time
    words_per_second = float(paras["batch_size"] * (paras["model_seq_len"]) * len(l_cost)) / elapsed
    perplexity_valid = networks.calc_perplexity_variable(x_valid, y_valid, paras, f_eval, padding=mask_valid)
    perplexity_train = np.exp(np.sum(l_cost) / len(l_cost))

    print("Epoch           : ", (epoch))
    print("Perplexity train: ", (perplexity_train))
    print("Perplexity valid: ", (perplexity_valid))
    print("Norm            : ", (sum(l_norm) / n_batches_train))
    print("Words per second: ", (words_per_second))

    if epoch >= (paras["no_decay_epochs"] - 1):
        current_lr = sh_lr.get_value()
        new_lr = current_lr * float(paras["decay"])
        sh_lr.set_value(lasagne.utils.floatX(new_lr))
        print("----New Learning Rate: " + str(new_lr))

    last_perplexity = perplexity_valid

    if perplexity_valid < best_valid_perplexity:
        print("----New best perplexity: " + str(perplexity_valid))
        best_valid_perplexity = perplexity_valid
        corresponding_train_perplexity = perplexity_train

        with open(os.path.join(paras["save_dir"], save_file_model + "_best"), 'wb') as f:
            cPickle.dump(lasagne.layers.get_all_param_values(l_out), f,
                         cPickle.HIGHEST_PROTOCOL)
    with  codecs.open(os.path.join(paras["save_dir"], save_file_settings), "a") as f:
        f.write("Epoch %s, valid perplexity %s, train_perplexity %s\n" % (
        epoch, perplexity_valid, perplexity_train))

    l_cost = []
    batch_time = 0

with open(os.path.join(paras["save_dir"], save_file_model + "_last"), 'wb') as f:
    cPickle.dump(lasagne.layers.get_all_param_values(l_out), f,
                 cPickle.HIGHEST_PROTOCOL)

print(paras)
print("Best Valid Perplexity = %s, with Train Perplexity = %s" % (
best_valid_perplexity, corresponding_train_perplexity))
