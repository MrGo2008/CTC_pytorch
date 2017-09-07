#!/usr/bin/python
#encoding=utf-8

#train process for the model

from data_prepare.data_loader import myDataset
from data_prepare.data_loader import myDataLoader
from model import *
from ctcDecoder import Decoder
from warpctc_pytorch import CTCLoss
import torch
import torch.nn as nn
from torch.autograd import Variable
import time 


def train(model, train_loader, loss_fn, optimizer, print_every=10):
    model.train()
    
    total_loss = 0
    i = 0
    for data in train_loader:
        inputs, targets, input_sizes, input_sizes_list, target_sizes = data
        inputs = inputs.transpose(0,1)
        batch_size = inputs.size(1)
        inputs = Variable(inputs, requires_grad=False)
        targets = Variable(targets, requires_grad=False)
        input_sizes = Variable(input_sizes, requires_grad=False)
        target_sizes = Variable(target_sizes, requires_grad=False)

        if USE_CUDA:
            inputs = inputs.cuda()
        
        #pack padded input sequence
        inputs = nn.utils.rnn.pack_padded_sequence(inputs, input_sizes_list)

        out = model(inputs)

        loss = loss_fn(out, targets, input_sizes, target_sizes)
        loss /= batch_size

        if (i + 1) % print_every == 0:
            print('batch = %d, loss = %.4f' % (i+1, loss.data[0]))
            logger.debug('batch = %d, loss = %.4f' % (i+1, loss.data[0]))
        
        total_loss += loss.data[0]
        optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm(model.parameters(), 400)
        optimizer.step()
        i += 1
    average_loss = total_loss / i
    print("Epoch done, average loss: %.4f" % average_loss)
    logger.info("Epoch done, average loss: %.4f" % average_loss)

def dev(model):
    model.eval()
    total_cer = 0
    total_tokens = 0
    decoder  = Decoder("#'acbedgfihkjmlonqpsrutwvyxz_", space_idx=28, blank_index=0)
    
    dev_dataset = myDataset(data_set='dev', n_mfcc=39)
    dev_loader = myDataLoader(dev_dataset, batch_size=4, shuffle=False,
                    num_workers=4, pin_memory=False)

    for data in dev_loader:
        inputs, targets, input_sizes, input_sizes_list, target_sizes =data
        inputs = inputs.transpose(0, 1)
        batch_size = inputs.size(1)
        inputs = Variable(inputs, volatile=True, requires_grad=False)
        if USE_CUDA:
            inputs = inputs.cuda()
        
        inputs = nn.utils.rnn.pack_padded_sequence(inputs, input_sizes_list)
        probs = model(inputs)
        
        probs = probs.data.cpu()
        total_cer += decoder.phone_word_error(probs, input_sizes_list, targets, target_sizes)[0]
        total_tokens += sum(target_sizes)
    acc = 1 - float(total_cer) / total_tokens
    return acc*100

def init_logger(log_file):
    import logging
    from logging.handlers import RotatingFileHandler

    logger = logging.getLogger()
    hdl = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=10)
    formatter=logging.Formatter('%(asctime)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s')
    hdl.setFormatter(formatter)
    logger.addHandler(hdl)
    logger.setLevel(logging.DEBUG)
    return logger

def main(init_lr):
    global logger
    log_file = './log/train.log'
    logger = init_logger(log_file)
    
    params = dict()
    params['rnn_input_size'] = 39
    params['rnn_hidden_size'] = 256
    params['rnn_layers'] = 5
    params['rnn_type'] = nn.GRU
    params['num_class'] = 28
    params['bidirectional'] = True
    params['batch_norm'] = True

    params['num_epoches'] = 40
    params['least_train_epoch'] = 15
    end_adjust_acc = 0.05
    params['decay'] = 0.1
    decay = 0.5
    count = 0
    params['learning_rate'] = init_lr
    params['batch_size'] = 4
    learning_rate = init_lr
    
    acc_best = -100
    adjust_rate_flag = False
    stop_train = False

    model = CTC_RNN(rnn_input_size=params['rnn_input_size'], rnn_hidden_size=params['rnn_hidden_size'], rnn_layers=params['rnn_layers'], 
                    rnn_type=params['rnn_type'], bidirectional=params['bidirectional'], batch_norm=params['batch_norm'], 
                    num_class=params['num_class'])
    if USE_CUDA:
        model = model.cuda()
    
    loss_fn = CTCLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=init_lr)
    
    start_time = time.time()
    while not stop_train:
        if count >= params['num_epoches']:
            break
        count += 1
        print("Start training epoch: %d" % count)
        logger.info("Start training epoch: %d" % count)
        if adjust_rate_flag:
            learning_rate *= decay
            for param in optimizer.param_groups:
                param['lr'] *= decay

        train_dataset = myDataset(data_set='train', n_mfcc=39)
        train_loader = myDataLoader(train_dataset, batch_size=4, shuffle=True,
                    num_workers=4, pin_memory=False)
        train(model, train_loader, loss_fn, optimizer, print_every=20)
        acc = dev(model)
        model_path_accept = './log/epoch'+str(count)+'_lr'+str(learning_rate)+'_cv'+str(acc)+'.pkl'
        model_path_reject = './log/epoch'+str(count)+'_lr'+str(learning_rate)+'_cv'+str(acc)+'_rejected.pkl'
        
        ##15轮迭代之后，开始调整学习率
        if count >= params['least_train_epoch']:
            model_state = model.state_dict()
            op_state = optimizer.state_dict()
            adjust_rate_flag = True

            if acc > (acc_best + end_adjust_acc):            
                acc_best = acc
                torch.save(model_state, model_path_accept)
            elif (acc > acc_best):
                stop_train = True
                acc_best = acc
                torch.save(model_state, model_path_accept)
            elif (acc <= acc_best):
                torch.save(model.state_dict(), model_path_reject)
                model.load_state_dict(model_state)
                optimizer.load_state_dict(op_state)
        
        time_used = (time.time() - start_time) / 60
        print("epoch %d done, cv acc is: %.4f, time_used: %.4f minutes" % (count, acc, time_used))
        logger.info("epoch %d done, cv acc is: %.4f, time_used: %.4f minutes" % (count, acc, time_used))
    print("End training, best cv acc is: %.4f" % acc_best)
    logger.info("End training, best cv acc is: %.4f" % acc_best)
    best_path = './log/best_model'+'_cv'+str(acc_best)+'.pkl'
    torch.save(model_state, best_path)
    params['acc best for dev']=acc_best
    result_record(params)

def result_record(params):
    record_file = 'training_record_for_timit'
    date = time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(time.time()))
    f = open(record_file, 'a+')
    f.writelines("Experiment "+date+':\n')
    for x in params:
        f.writelines('\t'+x+': '+str(params[x])+'\n')
    f.close()

if __name__ == '__main__':
    main(0.001)
