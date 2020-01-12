import os
import sys
import cv2
import csv
import time
import json
import torch
import string
import random
import PIL.Image
import numpy as np
from collections import deque
from operator import itemgetter
from sklearn.utils.linear_assignment_ import linear_assignment

from pprint import pprint

import trt_pose.coco
import trt_pose.models
from torch2trt import TRTModule
import torchvision.transforms as transforms
from trt_pose.parse_objects import ParseObjects
from trt_pose.draw_objects import DrawObjects

import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.optimizers import RMSprop

import pygame




w = 1024
h = 768
model_w = 224
model_h = 224

ASSET_DIR = '/home/jetson/trt_pose/tasks/human_pose/'
OPTIMIZED_MODEL = ASSET_DIR + 'resnet18_baseline_att_224x224_A_epoch_249_trt.pth'

body_labels = {0:'nose', 1: 'lEye', 2: 'rEye', 3:'lEar', 4:'rEar', 5:'lShoulder', 6:'rShoulder', 
               7:'lElbow', 8:'rElbow', 9:'lWrist', 10:'rWrist', 11:'lHip', 12:'rHip', 13:'lKnee', 14:'rKnee',
              15:'lAnkle', 16:'rAnkle', 17:'neck'}
body_idx = dict([[v,k] for k,v in body_labels.items()])

activity_dict = {'cross': 'walk', 'circle': 'wave', 'triangle': 'run', 'square': 'loiter'}
activity_idx = {0 :'loiter', 1: 'run', 2: 'walk', 3: 'wave'}
idx_dict = {x:idx for idx,x in enumerate(sorted(activity_dict.values()))}

with open(ASSET_DIR + 'human_pose.json', 'r') as f:
    human_pose = json.load(f)


def lstm_model():
    model = Sequential()
    model.add(LSTM(32, dropout=0.2, recurrent_dropout=0.2, input_shape=(pose_vec_dim, window)))
    model.add(Dense(32, activation='relu'))
    model.add(Dropout(0.2))
    model.add(Dense(len(activity_dict), activation='softmax'))
    print(model.summary())
    return model


WRITE2CSV = True
WRITE2VIDEO = True

if WRITE2CSV:
    #secondary_model = tf.keras.models.load_model('models/lstm_69.h5')
    window = 3
    pose_vec_dim = 36
    secondary_model = lstm_model()
    secondary_model.compile(loss='categorical_crossentropy', optimizer=RMSprop(lr=0.0001), metrics=['accuracy'])
    #activity_dict = {0: 'spin', 1: 'squat'}

    #activity = os.path.basename(source)
    #dataFile = open('data/{}.csv'.format(activity),'w')
    dataFile = open('data/{}.csv'.format(int(time.time() * 1000)),'w')
    newFileWriter = csv.writer(dataFile)

if WRITE2VIDEO:
    # Define the codec and create VideoWriter object
    name = 'out.mp4'
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(name, fourcc, 30.0, (w, h))

model_trt = TRTModule()
model_trt.load_state_dict(torch.load(OPTIMIZED_MODEL))

mean = torch.Tensor([0.485, 0.456, 0.406]).cuda()
std = torch.Tensor([0.229, 0.224, 0.225]).cuda()
device = torch.device('cuda')

topology = trt_pose.coco.coco_category_to_topology(human_pose)
parse_objects = ParseObjects(topology)
draw_objects = DrawObjects(topology)

####################################################

# Joystick initialization
os.environ["SDL_VIDEODRIVER"] = "dummy"
pygame.init()
pygame.joystick.init()
pygame.joystick.Joystick(0).init()

# Prints the joystick's name
JoyStick = pygame.joystick.Joystick(0)
JoyName = pygame.joystick.Joystick(0).get_name()
print("Name of the joystick:")
print(JoyName)

def getKeysByValue(dictOfElements, valueToFind):
    listOfKeys = list()
    listOfItems = dictOfElements.items()
    for item  in listOfItems:
        if item[1] == valueToFind:
            listOfKeys.append(item[0])
    return  listOfKeys

def getButton():
    pygame.event.pump()
    button_states = {'cross':0, 'circle':0, 'triangle':0, 'square':0}
    button_states['cross'] = JoyStick.get_button(14)
    button_states['circle'] = JoyStick.get_button(13)
    button_states['triangle'] = JoyStick.get_button(12)
    button_states['square'] = JoyStick.get_button(15)
    button = getKeysByValue(button_states, 1)
    return button

#############################################

def id_gen(size=6, chars=string.ascii_uppercase + string.digits):
    '''
    https://pythontips.com/2013/07/28/generating-a-random-string/
    input: id_gen(3, "6793YUIO")
    output: 'Y3U'
    '''
    return ''.join(random.choice(chars) for x in range(size))

def preprocess(image):
    global device
    device = torch.device('cuda')
    image = cv2.resize(image, (model_h, model_w))
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    image = PIL.Image.fromarray(image)
    image = transforms.functional.to_tensor(image).to(device)
    image.sub_(mean[:, None, None]).div_(std[:, None, None])
    return image[None, ...]

def inference(image):
    data = preprocess(image)
    cmap, paf = model_trt(data)
    cmap, paf = cmap.detach().cpu(), paf.detach().cpu()
    counts, objects, peaks = parse_objects(cmap, paf) #, cmap_threshold=0.15, link_threshold=0.15)
    body_dict = draw_objects(image, counts, objects, peaks)
    return image, body_dict


def IOU(boxA, boxB):
    # pyimagesearch: determine the (x, y)-coordinates of the intersection rectangle
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])

    # compute the area of intersection rectangle
    interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)

    # compute the area of both the prediction and ground-truth
    # rectangles
    boxAArea = (boxA[2] - boxA[0] + 1) * (boxA[3] - boxA[1] + 1)
    boxBArea = (boxB[2] - boxB[0] + 1) * (boxB[3] - boxB[1] + 1)

    # compute the intersection over union by taking the intersection
    # area and dividing it by the sum of prediction + ground-truth
    # areas - the interesection area
    iou = interArea / float(boxAArea + boxBArea - interArea)

    # return the intersection over union value
    return iou


def get_bbox(kp_list):
    bbox = []
    for aggs in [min, max]:
        for idx in range(2):
            bound = aggs(kp_list, key=itemgetter(idx))[idx]
            bbox.append(bound)
    return bbox

def tracker_match(trackers, detections, iou_thrd = 0.3):
    '''
    From current list of trackers and new detections, output matched detections,
    unmatched trackers, unmatched detections.
    https://towardsdatascience.com/computer-vision-for-tracking-8220759eee85
    '''

    IOU_mat= np.zeros((len(trackers),len(detections)),dtype=np.float32)
    for t,trk in enumerate(trackers):
        for d,det in enumerate(detections):
            IOU_mat[t,d] = IOU(trk,det)

    # Produces matches
    # Solve the maximizing the sum of IOU assignment problem using the
    # Hungarian algorithm (also known as Munkres algorithm)

    matched_idx = linear_assignment(-IOU_mat)

    unmatched_trackers, unmatched_detections = [], []
    for t,trk in enumerate(trackers):
        if(t not in matched_idx[:,0]):
            unmatched_trackers.append(t)

    for d, det in enumerate(detections):
        if(d not in matched_idx[:,1]):
            unmatched_detections.append(d)

    matches = []

    # For creating trackers we consider any detection with an
    # overlap less than iou_thrd to signifiy the existence of
    # an untracked object

    for m in matched_idx:
        if(IOU_mat[m[0],m[1]] < iou_thrd):
            unmatched_trackers.append(m[0])
            unmatched_detections.append(m[1])
        else:
            matches.append(m.reshape(1,2))

    if(len(matches)==0):
        matches = np.empty((0,2),dtype=int)
    else:
        matches = np.concatenate(matches,axis=0)

    return matches, np.array(unmatched_detections), np.array(unmatched_trackers)

class PersonTracker(object):
    def __init__(self, expiration=5):
        self.count = 0
        self.activity = ['walk']
        self.expiration = expiration
        self.id = id_gen() #int(time.time() * 1000)
        self.q = deque(maxlen=10)
        return
        
    def set_bbox(self, bbox):
        self.bbox = bbox
        x1, y1, x2, y2 = bbox
        self.h = 1e-6 + x2 - x1
        self.w = 1e-6 + y2 - y1
        self.centroid = tuple(map(int, ( x1 + self.h / 2, y1 + self.w / 2)))
        return

    def update_pose(self, pose_dict):
        ft_vec = np.zeros(2 * len(body_labels))
        for ky in pose_dict:
            idx = body_idx[ky]
            ft_vec[2 * idx: 2 * (idx + 1)] = 2 * (np.array(pose_dict[ky]) - np.array(self.centroid)) / np.array((self.h, self.w))
        self.q.append(ft_vec)
        return

    def annotate(self, image, offset=50):
        x1, y1, x2, y2 = self.bbox
        image = cv2.rectangle(image, (x1 - offset, y1 - offset), (x2 + offset, y2 + offset), (195, 195, 89), 2) 
        image = cv2.putText(image, self.activity.upper(), (x1 - offset + 10, y1 - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (151, 187, 106), 2) 
        image = cv2.putText(image, self.id, (x1 - offset + 10, y1 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (151, 187, 106), 2) 
        image = cv2.drawMarker(image, self.centroid, (223, 183, 190), 0, 30, 3) 
        return image




source = sys.argv[1]
source = int(source) if source.isdigit() else source
cap = cv2.VideoCapture(source)

fourcc_cap = cv2.VideoWriter_fourcc(*'MJPG')
cap.set(cv2.CAP_PROP_FOURCC, fourcc_cap)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)



trackers = []
while True:

    ret, frame = cap.read()
    bboxes = []
    if ret:

        image, pose_list = inference(frame)
        for body in pose_list:
            bbox = get_bbox(list(body.values()))
            bboxes.append((bbox, body))

        track_boxes = [tracker.bbox for tracker in trackers]
        matched, unmatched_trackers, unmatched_detections = tracker_match(track_boxes, [b[0] for b in bboxes])

        for idx, jdx in matched:
            trackers[idx].set_bbox(bboxes[jdx][0])
            trackers[idx].update_pose(bboxes[jdx][1])

        for idx in unmatched_detections:
            try:
                trackers[idx].count += 1
            except:
                pass
            try:
                if trackers[idx].count > trackers[idx].expiration:
                    trackers.pop(idx)
            except:
                pass

        for idx in unmatched_trackers:
            person = PersonTracker()
            person.set_bbox(bboxes[idx][0])
            person.update_pose(bboxes[idx][1])
            trackers.append(person)

        pprint([(tracker.id, np.vstack(tracker.q)) for tracker in trackers])

        for tracker in trackers:
            #print(len(tracker.q))
            activity = [activity_dict[x] for x in getButton()]
            print('-------------------------')
            if len(tracker.q) >= 3:
                sample = np.array(list(tracker.q)[:3])
                sample = sample.reshape(1, pose_vec_dim, window)
                if activity:
                    #activity_y = tf.keras.utils.to_categorical(list(map(idx_dict.get, tracker.activity)), len(activity_dict))
                    activity_y = np.expand_dims(tf.keras.utils.to_categorical(idx_dict[activity[0]], len(activity_dict)), axis=0)
                    print(activity_y.shape)
                    secondary_model.fit(sample, activity_y, batch_size=1, epochs=1, verbose=1)
                    tracker.activity = activity
                else:
                    pred_activity = activity_idx[np.argmax(secondary_model.predict(sample)[0])]
                    tracker.activity = pred_activity

                print(tracker.activity)

            if WRITE2CSV:
                newFileWriter.writerow([tracker.activity] + list(np.hstack(list(tracker.q)[:3])))
                    

        if WRITE2VIDEO:
            for tracker in trackers:
                if len(tracker.q) >= 3:
                    image = tracker.annotate(image)
            out.write(image)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    else:
        break

cap.release()

try:
    dataFile.close()
except:
    pass

try:
    out.release()
except:
    pass