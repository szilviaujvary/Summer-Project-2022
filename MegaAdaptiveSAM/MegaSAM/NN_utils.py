import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import datasets, transforms
from tqdm.notebook import tqdm, trange
# from sklearn import datasets
from tqdm.notebook import tqdm
import matplotlib.pyplot as plt
device = "cuda" if torch.cuda.is_available() else "cpu"
from sam.sam import SAM
from mega_sam_feri import MegaSAM

import sys; sys.path.append("..")
sys.path.append("sam")

def effective_rank(tensor):
    _, s,_ = torch.svd(tensor.float())
    norm = torch.sum(torch.abs(s))
    entropy = 0
    for sv in list(s):
        sv = float(sv)
        entropy -= sv * np.log(sv/norm)/norm
    return np.exp(entropy)
# Testing
# tensor = torch.tensor([[1,  0],[0,  1]])
# print(effective_rank(tensor))

def get_n_params(model):
    """Gets the number of parameters of a model."""
    pp=0
    for p in list(model.parameters()):
        nn=1
        for s in list(p.size()):
            nn = nn*s
        pp += nn
    return pp

def flatten_and_together(list_of_tensors):
    """Takes a list of tensors and put them in one tensor, flattened."""
    new_list = []
    for tensor in list_of_tensors:
        new_list.append(torch.flatten(tensor))
    return torch.cat(new_list)


def train_multi_model(model, train_data, test_data, optim='SGD', batch_size=32, epochs=10, tracking=False,
                shuffle_loader=True, lr=0.01, momentum=0.9, criterion=nn.CrossEntropyLoss(),
                rho=0.05, alpha=None, lr_M=1):
    """
    Trains a model for classification.

    Inputs:
    model: an intantiation of the class of the model we want to train.
    tracking: tracks test accuracy during training, Boolean
    optimizer: String, can be 'SGD', 'SAM', 'Adam', MegaSAM
    rho: float, the rho parameter for SAM
    """
    from torchvision import datasets, transforms
    from tqdm.notebook import tqdm, trange
    numofparams = get_n_params(model)

    train_loader = torch.utils.data.DataLoader(train_data, batch_size=batch_size, shuffle=shuffle_loader)
    # test_loader = torch.utils.data.DataLoader(test_data, batch_size=batch_size, shuffle=False)
    val_loader = torch.utils.data.DataLoader(test_data, shuffle=False)

    if optim == 'SGD':
        optimizer = torch.optim.SGD(model.parameters(), lr=lr, momentum=momentum)
    if optim == 'Adam':
        optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    if optim == 'SAM':
        base_optimizer = torch.optim.SGD
        optimizer = SAM(model.parameters(), base_optimizer,rho=rho, lr = lr, momentum=momentum)
    if optim == 'MegaSAM':
        base_optimizer = torch.optim.SGD
        optimizer = MegaSAM(model.parameters(), base_optimizer=base_optimizer, lr=lr, lr_M=lr_M, alpha=alpha)

    training_losses = []
    training_accuracies = []
    validation_accuracies = []

    # Iterate through train set minibatchs
 
    for epoch in trange(epochs):  
        per_epoch_loss = 0
        correct = 0
        for numbers, labels in train_loader:
            x = numbers[:,None]
            x = x.to(device)
            labels = labels.to(device)[:,None]
            # Zero out the gradients
            optimizer.zero_grad()
            if optim == "SAM" or optim == 'MegaSAM':
                def closure():
                    z = model(x.float())
                    y = z.reshape((z.shape[0],10,1))
                    y = y.double()
                    loss = criterion(y, labels.long())
                    loss.backward()
                    return loss
            # Forward pass
            z = model(x.float())
            y = z.reshape((z.shape[0],10,1))

            y = y.double()
            labels = labels.long()
            loss = criterion(y, labels)

            if tracking:
                # Tracking loss
                per_epoch_loss += loss
                # Train accuracy tracking
                predictions = torch.argmax(y, dim=1)
                correct += torch.sum((predictions == labels).float())

            loss.backward()
            if optim == "SAM" or optim == 'MegaSAM':
                optimizer.step(closure)
            if optim != "SAM" and optim != 'MegaSAM':
                optimizer.step()
 

        if tracking:
            correct_test = 0
            with torch.no_grad():
                    # Iterate through test set minibatchs 
                    for numbers2, labels2 in val_loader:
                        numbers2 = numbers2.to(device)
                        labels2 = labels2.double().to(device)[:,None]
                        # Forward pass
                        x2 = numbers2[:,None]
                        y2 = model(x2)
                        predictions2 = torch.argmax(y2)
                        correct_test += torch.sum((predictions2 == labels2).float())

            training_losses.append(per_epoch_loss/len(train_loader))
            training_accuracies.append(correct/len(train_data))
            validation_accuracies.append(correct_test/len(test_data))
    # print(optimizer.M_param_groups[0]['params'][0])
    training_losses = [i.item() for i in training_losses]
    training_accuracies = [i.item() for i in training_accuracies]
    validation_accuracies = [i.item() for i in validation_accuracies]

    return model, training_losses, training_accuracies, validation_accuracies, optimizer

