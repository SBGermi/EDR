import sys
sys.path.append('..')
import os
import torch
import torch.nn.parallel
import torch.nn.functional as F
from torch.optim.lr_scheduler import MultiStepLR
import torchvision.transforms as transforms
import random
import copy
import argparse
from tqdm import tqdm
import numpy as np
from termcolor import cprint
from utils import lrt_correction
from networks.vgg import vgg19_bn
from animal_dataset import Animal10

def _init_fn(worker_id):
    np.random.seed(77 + worker_id)


def main(args):
    # Initialize random seeds
    random_seed = args.seed
    np.random.seed(random_seed)
    random.seed(random_seed)
    torch.manual_seed(random_seed)
    torch.cuda.manual_seed(random_seed)
    torch.cuda.manual_seed_all(random_seed)
    torch.backends.cudnn.deterministic = True

    # Select the decive (CPU or CUDA)
    os.environ["CUDA_VISIBLE_DEVICES"] = str(0)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # Set hyperparameters
    batch_size: int = 128
    num_workers: int = 2
    lr: float = 0.1
    current_thd: float = 0.3
    thd_increment: float = 0.1
    
    # Data augmentation
    transform_train = transforms.Compose([
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),])

    transform_test = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),])
  
    trainset = Animal10(root = 'data', split = 'train', transform = transform_train)
    trainloader = torch.utils.data.DataLoader(trainset, batch_size = batch_size, shuffle = True, num_workers = num_workers, worker_init_fn = _init_fn, drop_last = True)

    valset = Animal10(root = 'data', split = 'val', transform = transform_test)
    valloader = torch.utils.data.DataLoader(valset, batch_size = batch_size, shuffle = False, num_workers = num_workers)
    
    testset = Animal10(root = 'data', split = 'test', transform = transform_test)
    testloader = torch.utils.data.DataLoader(testset, batch_size = batch_size, shuffle = False, num_workers = num_workers)

    num_class = 10

    print('\nTrain set size:', len(trainset))
    print('\Valid set size:', len(valset))
    print('Test set size:', len(testset), '\n')

    # Setup network
    model = vgg19_bn(num_classes = num_class)
    model = torch.nn.DataParallel(model)

    print("============= Start Training =============")
    loss_func = torch.nn.CrossEntropyLoss()
    optimizer = torch.optim.SGD(model.parameters(), lr = lr, weight_decay = 1e-3)
    scheduler = MultiStepLR(optimizer, milestones = [50, 75], gamma = 0.2)
    model = model.to(device)
    
    output_record = torch.zeros([30, len(trainset), num_class])
    output_selected = torch.zeros([10, len(trainset), num_class])
    val_record = torch.zeros([30])

    best_acc = 0
    best_epoch = 0
    best_weights = None

    for epoch in range(args.nepoch):
        # Training
        train_loss = 0
        train_correct = 0
        train_total = 0

        model.train()
        for _, (images, labels, indices) in enumerate(tqdm(trainloader, ascii = True, ncols = 50)):
            if images.size(0) == 1:
                continue

            images, labels = images.to(device), labels.to(device)
            optimizer.zero_grad()
            outputs = model(images)
            loss = loss_func(outputs, labels)     
            loss.backward()
            optimizer.step()
            train_loss += loss.item()
            train_total += images.size(0)
            _, predicted = outputs.max(1)
            train_correct += predicted.eq(labels).sum().item()
            output_record[epoch % 30, indices] = F.softmax(outputs.detach().cpu(), dim = 1)

        train_acc = train_correct / train_total * 100.

        # Validation
        val_total = 0
        val_correct = 0
        model.eval()
        with torch.no_grad():
            for i, (images, labels, _) in enumerate(valloader):
                images, labels = images.to(device), labels.to(device)
                outputs = model(images)
                val_total += images.size(0)
                _, predicted = outputs.max(1)
                val_correct += predicted.eq(labels).sum().item()

        val_acc = val_correct / val_total * 100.
        val_record[epoch % 30] = val_acc

        if val_acc > best_acc:
            best_acc = val_acc
            best_epoch = epoch
            best_weights = copy.deepcopy(model.state_dict())
        
        ind = np.argsort(val_record)[-10:]
        
        cprint("Epoch {}|{}. Train accuracy: {:.2f}%  Val accuracy: {:.2f}".format(epoch + 1, args.nepoch, train_acc, val_acc), "yellow")
        cprint('>> Top 10 accuracies: {}'.format(np.array(val_record)[ind]), 'green')

        # Correction
        if epoch >= 40:
            output_selected = output_record[ind].mean(0)
            y_corrected, current_thd = lrt_correction(np.array(trainset.targets).copy(), output_selected, current_thd = current_thd, thd_increment = thd_increment)
            trainset.update_corrupted_label(y_corrected)

        scheduler.step()

    # testing
    test_total = 0
    test_correct = 0
    model.load_state_dict(best_weights)
    model.eval()

    with torch.no_grad():
        for i, (images, labels, _) in enumerate(testloader):
            images, labels = images.to(device), labels.to(device)
            outputs = model(images)
            test_total += images.size(0)
            _, predicted = outputs.max(1)
            test_correct += predicted.eq(labels).sum().item()

    test_acc = test_correct / test_total * 100.
    cprint('>> Final test accuracy: {:.2f}'.format(test_acc), 'cyan')

    return test_acc
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--nepoch", default = 100, help = "number of training epochs", type = int)
    parser.add_argument("--seed", default = 77, help = "random seed", type = int)
    args = parser.parse_args()
    main(args)
