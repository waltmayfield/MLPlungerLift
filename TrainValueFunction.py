
import re; import sys; import importlib; import tqdm; import os; import time; import glob
import numpy as np; import pandas as pd
import tensorflow as tf
from tensorflow import keras
from tensorflow.keras.models import load_model
from tensorflow.keras.layers import LeakyReLU
from tensorflow.keras.callbacks import ModelCheckpoint
from tensorflow.keras.optimizers import Adam

import FunctionsTF as F
import Metrics as M

print(f'Tensorflow version: {tf.__version__}')
lGpus = tf.config.experimental.list_physical_devices('GPU')
print(f'GPUs: {lGpus}')

homeDirectory = f'/AttachedVol/EBSPlungerFiles/'

model_name = r'20201216_460k_Param_LSTM_Skip_resBlock_311Epoch.h5'
model_save_location = homeDirectory + r'Models/' + model_name
output_model_save_location = homeDirectory + r'Models/' + r'20201216_460k_Param_LSTM_Skip_resBlock.h5'

historyPath = homeDirectory + r'LossCurves/' + r'20201216History.csv'

bucket_name = 'hilcorp-l48operations-plunger-lift-main'

#This creates a new hitory df if one does not already exist
if not os.path.isfile(historyPath):
    print('Creating new history DF at {}'.format(time.localtime()))
    pd.DataFrame(columns = ['loss', 'MCF_metric', 'plunger_speed_metric', 'val_loss', 'val_MCF_metric', 'val_plunger_speed_metric']).to_csv(historyPath, index = False)
    
model = load_model(model_save_location, compile = False, custom_objects = {'LeakyReLU' : LeakyReLU()})
print('########## Model Summary #############')
print(model.summary())# tf.keras.utils.plot_model(model,show_shapes=True)

## This gets the most recent data file
list_of_files = glob.glob(homeDirectory + f'/TFRecordFiles/*') # * means all if need specific format then *.csv
latest_file = max(list_of_files, key=os.path.getctime) #This gets the most recently uploaded TF Record File
lTFRecordFiles = [latest_file]
print(f'Most Recent TFRecord File: {lTFRecordFiles}')

######################### Training Parameters ########################
validation_split = 0.1
batch_size = 2
num_parallel_calls = 8
buffer_size = 8
######################################################################

def count_data_items(filenames):
    'Counts the records in each file name'
    n = [int(re.compile(r"-([0-9]*)-Records\.").search(filename).group(1)) for filename in filenames]
#     print(n)
    return np.sum(n)

num_examples = count_data_items(lTFRecordFiles)
numTrainWells = int(np.floor(num_examples*(1.-validation_split)))
numValidWells = num_examples - numTrainWells
print(f"Number of training wells: {numTrainWells}, Validation wells: {numValidWells} of total wells {num_examples}")

############# This Makes the data set ######
raw_dataset = tf.data.TFRecordDataset(lTFRecordFiles)
# allWellDs = allWellDs.map(lambda x, y: (x[:100,:],y[:100,:]))#This is just for testing purposes to trim X for shorter computation

trainDs = raw_dataset.skip(numValidWells)
trainDs = trainDs.map(F.parse_raw_examples_UWI, num_parallel_calls=num_parallel_calls)
trainDs = trainDs.map(lambda x, y, UWIs: (x,y))#This is to remove the UWI which is not useful in trianing
trainDs = trainDs.map(lambda x, y: (tf.reverse(x, axis = [0]),tf.reverse(y, axis = [0])))#This is to have leading instead of trailing 0s. Reverse the time direction
trainDs = trainDs.padded_batch(batch_size, padded_shapes=([None,79],[None,2]))# Add the 0s behind the example
trainDs = trainDs.map(lambda x, y: (tf.reverse(x, axis = [1]),tf.reverse(y, axis = [1]))) #Reverse the time to the correct direction
trainDs = trainDs.prefetch(buffer_size)
# trainDs = trainDs.cache(r'./')

validDs = raw_dataset.take(numValidWells)
validDs = validDs.map(F.parse_raw_examples_UWI, num_parallel_calls=num_parallel_calls)
validDs = validDs.map(lambda x, y, UWIs: (x,y))#This is to remove the UWI which is not useful in trianing
validDs = validDs.map(lambda x, y: (tf.reverse(x, axis = [0]),tf.reverse(y, axis = [0])))
validDs = validDs.padded_batch(batch_size, padded_shapes=([None,79],[None,2]))
validDs = validDs.map(lambda x, y: (tf.reverse(x, axis = [1]),tf.reverse(y, axis = [1])))
validDs = validDs.prefetch(buffer_size)


print('Clocking training DS Speed')
for x in tqdm.tqdm(trainDs.take(20)): pass #This is to clock data set speed
print('Clocking validation DS Speed')
for x in tqdm.tqdm(validDs.take(20)): pass #This is to clock data set speed



####### Here are the Check Points ######
class EpochLogger(tf.keras.callbacks.Callback):
    def __init__(self,historyPath):
        self.historyPath = historyPath
    def on_epoch_end(self,epoch,logs=None):#This saves the loss data
        lossDf = pd.DataFrame(logs, index = [0]) #Turns logs into dataframe
        lossDf.to_csv(self.historyPath, mode = 'a', header = False, index = False) #Appends to existing csv file

model_checkpoint = ModelCheckpoint(output_model_save_location, 
                                   monitor = 'loss', 
                                   save_best_only=False, 
                                   save_weights_only = False,
                                   verbose=1)

terminateOnNaN = keras.callbacks.TerminateOnNaN()
log_results = EpochLogger(historyPath)

optimizer = Adam(lr = 1e-3)
model.compile(loss=M.custom_loss, optimizer=optimizer, metrics = [M.MCF_metric, M.plunger_speed_metric])

steps_per_epoch = int(np.ceil(numTrainWells/batch_size))

model.fit(x = trainDs.repeat(epochs),
          validation_data=validDs,
          epochs = 1000,
          steps_per_epoch = steps_per_epoch,
          use_multiprocessing=False,
          callbacks = [
            log_results,
            model_checkpoint,
            terminateOnNaN
            ]
          )
