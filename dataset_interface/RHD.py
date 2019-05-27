# coding=UTF-8
import pickle

import os

import numpy as np
from tqdm import tqdm
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D

from dataset_interface.RHD_canonical_trafo import canonical_trafo, flip_right_hand
from dataset_interface.RHD_general import crop_image_from_xy
from dataset_interface.RHD_relative_trafo import bone_rel_trafo
from dataset_interface.base_interface import BaseDataset
import tensorflow as tf
class RHD(BaseDataset):
    def __init__(self, path="/media/chen/4CBEA7F1BEA7D1AE/Download/hand_dataset/ICCV2017/RHD_published_v2", batchnum = 4):
        super(RHD, self).__init__(path)

        # 将标签加载到内存中
        with open(self.path+"/training/anno_training.pickle", 'rb') as fi:
            anno_all = pickle.load(fi)
        self.allxyz = np.zeros(shape=[len(anno_all), 42, 3], dtype=np.float32)
        self.alluv = np.zeros(shape=[len(anno_all), 42, 3], dtype=np.float32)
        self.allk = np.zeros(shape=[len(anno_all), 3, 3], dtype=np.float32)
        for label_i in range(len(anno_all)):
            self.allxyz[label_i,:,:] = anno_all[label_i]['xyz']
            self.alluv[label_i, :, :] = anno_all[label_i]['uv_vis']
            self.allk[label_i, :, :] = anno_all[label_i]['K']

        # 将图像文件名列表加载到内存中
        imagefilenames = BaseDataset.listdir(self.path+"/training/color")
        self.example_num = len(imagefilenames)
        self.imagefilenames = tf.constant(imagefilenames)

        maskfilenames = BaseDataset.listdir(self.path+"/training/mask")
        self.maskfilenames = tf.constant(maskfilenames)

        assert self.example_num == len(anno_all), '标签和样本数量不一致'

        # 创建数据集
        dataset = tf.data.Dataset.from_tensor_slices((self.imagefilenames, self.maskfilenames, self.allxyz, self.alluv, self.allk))
        dataset = dataset.map(RHD._parse_function)
        dataset = dataset.repeat()
        dataset = dataset.shuffle(buffer_size=40)
        self.dataset = dataset.batch(batchnum)
        #self.iterator = self.dataset.make_initializable_iterator() sess.run(dataset_RHD.iterator.initializer)
        self.iterator = self.dataset.make_one_shot_iterator()
        self.get_batch_data = self.iterator.get_next()

    @staticmethod
    def visualize_data(image, xyz, uv, uv_vis, k, num_px_left_hand, num_px_right_hand, scoremap):
        # get info from annotation dictionary
        kp_coord_uv = uv[:, :2]  # u, v coordinates of 42 hand keypoints, pixel
        kp_visible =(uv_vis == 1)  # visibility of the keypoints, boolean
        kp_coord_xyz = xyz  # x, y, z coordinates of the keypoints, in meters
        camera_intrinsic_matrix = k  # matrix containing intrinsic parameters
        scoremap = np.sum(scoremap, axis=-1)
        # Project world coordinates into the camera frame
        kp_coord_uv_proj = np.matmul(kp_coord_xyz, np.transpose(camera_intrinsic_matrix))
        kp_coord_uv_proj = kp_coord_uv_proj[:, :2] / kp_coord_uv_proj[:, 2:]

        # Visualize data
        fig = plt.figure(1)
        ax1 = fig.add_subplot('221')
        ax1.set_title("left_hand:"+str(num_px_left_hand)+"right_hand:"+str(num_px_right_hand))
        ax2 = fig.add_subplot('222')
        ax3 = fig.add_subplot('223')
        ax4 = fig.add_subplot('224', projection='3d')

        ax1.imshow(image)
        ax1.scatter(kp_coord_uv[kp_visible, 0], kp_coord_uv[kp_visible, 1], marker='o', color='blue', s=5)
        image = (image + 0.5) * 255
        image = image.astype(np.int16)
        ax2.scatter(kp_coord_uv_proj[:, 0], kp_coord_uv_proj[:, 1], marker='x', color='red', s=5)
        ax2.imshow(image)


        ax3.imshow(scoremap)
        ax4.scatter(kp_coord_xyz[:, 0], kp_coord_xyz[:, 1], kp_coord_xyz[:, 2])
        ax4.view_init(azim=-90.0, elev=-90.0)  # aligns the 3d coord with the camera view
        ax4.set_xlabel('x')
        ax4.set_ylabel('y')
        ax4.set_zlabel('z')

        plt.show()

    @staticmethod
    def _parse_function(imagefilename, maskfilename, keypoint_xyz, keypoint_uv, k):
        # 数据的基本处理
        image_size = (320, 320)
        image_string = tf.read_file(imagefilename)
        image_decoded = tf.image.decode_png(image_string)
        image_decoded = tf.image.resize_images(image_decoded, [image_size[0],image_size[0]],method=0)
        image = tf.cast(image_decoded, tf.float32)
        image = image / 255.0 - 0.5

        mask_string = tf.read_file(maskfilename)
        mask_decoded = tf.image.decode_png(mask_string)
        hand_parts_mask = tf.cast(mask_decoded, tf.int32)

        keypoint_vis = tf.cast(keypoint_uv[:, 2], tf.bool)
        keypoint_uv = keypoint_uv[:, :2]

        """参数
        
        """
        # general parameters
        batch_size = 4

        coord_uv_noise = True
        coord_uv_noise_sigma = 1.0  # std dev in px of noise on the uv coordinates
        crop_center_noise = True
        crop_center_noise_sigma = 20.0  # std dev in px: this moves what is in the "center", but the crop always contains all keypoints
        crop_offset_noise = False
        crop_offset_noise_sigma = 10.0  # translates the crop after size calculation (this can move keypoints outside)
        crop_scale_noise_ = False
        crop_size = 192
        hand_crop = True
        hue_aug = False
        hue_aug_max = 0.1

        num_kp = 42
        num_samples = 41258
        random_crop_size = 256
        random_crop_to_size = False
        scale_target_size = (240, 320)  # size its scaled down to if scale_to_size=True
        scale_to_size = False
        scoremap_dropout = False
        scoremap_dropout_prob = 0.8
        sigma = 25.0
        shuffle = True
        use_wrist_coord = False

        # 使用掌心代替手腕
        if not use_wrist_coord:
            palm_coord_l = tf.expand_dims(0.5*(keypoint_xyz[0, :] + keypoint_xyz[12, :]), 0)
            palm_coord_r = tf.expand_dims(0.5*(keypoint_xyz[21, :] + keypoint_xyz[33, :]), 0)
            keypoint_xyz = tf.concat([palm_coord_l, keypoint_xyz[1:21, :], palm_coord_r, keypoint_xyz[-20:, :]], 0)

        if not use_wrist_coord:
            palm_coord_uv_l = tf.expand_dims(0.5*(keypoint_uv[0, :] + keypoint_uv[12, :]), 0)
            palm_coord_uv_r = tf.expand_dims(0.5*(keypoint_uv[21, :] + keypoint_uv[33, :]), 0)
            keypoint_uv = tf.concat([palm_coord_uv_l, keypoint_uv[1:21, :], palm_coord_uv_r, keypoint_uv[-20:, :]], 0)

        if coord_uv_noise:
            noise = tf.truncated_normal([42, 2], mean=0.0, stddev=coord_uv_noise_sigma)
            keypoint_uv += noise

        if hue_aug:
            image = tf.image.random_hue(image, hue_aug_max)

        # calculate palm visibility
        if not use_wrist_coord:
            palm_vis_l = tf.expand_dims(tf.logical_or(keypoint_vis[0], keypoint_vis[12]), 0)
            palm_vis_r = tf.expand_dims(tf.logical_or(keypoint_vis[21], keypoint_vis[33]), 0)
            keypoint_vis = tf.concat([palm_vis_l, keypoint_vis[1:21], palm_vis_r, keypoint_vis[-20:]], 0)

        # 数据的高级处理
        # figure out dominant hand by analysis of the segmentation mask
        one_map, zero_map = tf.ones_like(hand_parts_mask), tf.zeros_like(hand_parts_mask)
        cond_l = tf.logical_and(tf.greater(hand_parts_mask, one_map), tf.less(hand_parts_mask, one_map*18))
        cond_r = tf.greater(hand_parts_mask, one_map*17)
        hand_map_l = tf.where(cond_l, one_map, zero_map)
        hand_map_r = tf.where(cond_r, one_map, zero_map)
        num_px_left_hand = tf.reduce_sum(hand_map_l)
        num_px_right_hand = tf.reduce_sum(hand_map_r)

        # We only deal with the more prominent hand for each frame and discard the second set of keypoints
        kp_coord_xyz_left = keypoint_xyz[:21, :]
        kp_coord_xyz_right = keypoint_xyz[-21:, :]

        cond_left = tf.logical_and(tf.cast(tf.ones_like(kp_coord_xyz_left), tf.bool), tf.greater(num_px_left_hand, num_px_right_hand))
        kp_coord_xyz21 = tf.where(cond_left, kp_coord_xyz_left, kp_coord_xyz_right)

        hand_side = tf.where(tf.greater(num_px_left_hand, num_px_right_hand),
                             tf.constant(0, dtype=tf.int32),
                             tf.constant(1, dtype=tf.int32))  # left hand = 0; right hand = 1
        hand_side = tf.one_hot(hand_side, depth=2, on_value=1.0, off_value=0.0, dtype=tf.float32)

        keypoint_xyz21 = kp_coord_xyz21
        #对xyz标签进行标准化
        # make coords relative to root joint
        kp_coord_xyz_root = kp_coord_xyz21[0, :] # this is the palm coord
        kp_coord_xyz21_rel = kp_coord_xyz21 - kp_coord_xyz_root  # relative coords in metric coords
        index_root_bone_length = tf.sqrt(tf.reduce_sum(tf.square(kp_coord_xyz21_rel[12, :] - kp_coord_xyz21_rel[11, :])))
        keypoint_xyz21_normed = kp_coord_xyz21_rel / index_root_bone_length  # normalized by length of 12->11

        # calculate local coordinates
        kp_coord_xyz21_local = bone_rel_trafo(keypoint_xyz21_normed)
        kp_coord_xyz21_local = tf.squeeze(kp_coord_xyz21_local)
        keypoint_xyz21_local = kp_coord_xyz21_local

        # calculate viewpoint and coords in canonical coordinates
        kp_coord_xyz21_rel_can, rot_mat = canonical_trafo(keypoint_xyz21_normed)
        kp_coord_xyz21_rel_can, rot_mat = tf.squeeze(kp_coord_xyz21_rel_can), tf.squeeze(rot_mat)
        kp_coord_xyz21_rel_can = flip_right_hand(kp_coord_xyz21_rel_can, tf.logical_not(cond_left))
        keypoint_xyz21_can = kp_coord_xyz21_rel_can
        rot_mat = tf.matrix_inverse(rot_mat)

        # Set of 21 for visibility
        keypoint_vis_left = keypoint_vis[:21]
        keypoint_vis_right = keypoint_vis[-21:]
        keypoint_vis21 = tf.where(cond_left[:, 0], keypoint_vis_left, keypoint_vis_right)
        keypoint_vis21 = keypoint_vis21

        # Set of 21 for UV coordinates
        keypoint_uv_left = keypoint_uv[:21, :]
        keypoint_uv_right = keypoint_uv[-21:, :]
        keypoint_uv21 = tf.where(cond_left[:, :2], keypoint_uv_left, keypoint_uv_right)

        """ DEPENDENT DATA ITEMS: HAND CROP """
        if hand_crop:
            crop_center = keypoint_uv21[12, ::-1]

            # catch problem, when no valid kp available (happens almost never)
            crop_center = tf.cond(tf.reduce_all(tf.is_finite(crop_center)), lambda: crop_center,
                                  lambda: tf.constant([0.0, 0.0]))
            crop_center.set_shape([2, ])

            if crop_center_noise:
                noise = tf.truncated_normal([2], mean=0.0, stddev=crop_center_noise_sigma)
                crop_center += noise

            crop_scale_noise = tf.constant(1.0)
            if crop_scale_noise_:
                crop_scale_noise = tf.squeeze(tf.random_uniform([1], minval=1.0, maxval=1.2))

            # select visible coords only
            kp_coord_h = tf.boolean_mask(keypoint_uv21[:, 1], keypoint_vis21)
            kp_coord_w = tf.boolean_mask(keypoint_uv21[:, 0], keypoint_vis21)
            kp_coord_hw = tf.stack([kp_coord_h, kp_coord_w], 1)

            # determine size of crop (measure spatial extend of hw coords first)
            min_coord = tf.maximum(tf.reduce_min(kp_coord_hw, 0), 0.0)
            max_coord = tf.minimum(tf.reduce_max(kp_coord_hw, 0), image_size)

            # find out larger distance wrt the center of crop
            crop_size_best = 2*tf.maximum(max_coord - crop_center, crop_center - min_coord)
            crop_size_best = tf.reduce_max(crop_size_best)
            crop_size_best = tf.minimum(tf.maximum(crop_size_best, 50.0), 500.0)

            # catch problem, when no valid kp available
            crop_size_best = tf.cond(tf.reduce_all(tf.is_finite(crop_size_best)), lambda: crop_size_best,
                                  lambda: tf.constant(200.0))
            crop_size_best.set_shape([])

            # calculate necessary scaling
            scale = tf.cast(crop_size, tf.float32) / crop_size_best
            scale = tf.minimum(tf.maximum(scale, 1.0), 10.0)
            scale *= crop_scale_noise
            crop_scale = scale

            if crop_offset_noise:
                noise = tf.truncated_normal([2], mean=0.0, stddev=crop_offset_noise_sigma)
                crop_center += noise

            # Crop image
            img_crop = crop_image_from_xy(tf.expand_dims(image, 0), crop_center, crop_size, scale)
            image_crop = tf.stack([img_crop[0,:,:,0], img_crop[0,:,:,1],img_crop[0,:,:,2]],2)

            # Modify uv21 coordinates
            crop_center_float = tf.cast(crop_center, tf.float32)
            keypoint_uv21_u = (keypoint_uv21[:, 0] - crop_center_float[1]) * scale + crop_size // 2
            keypoint_uv21_v = (keypoint_uv21[:, 1] - crop_center_float[0]) * scale + crop_size // 2
            keypoint_uv21 = tf.stack([keypoint_uv21_u, keypoint_uv21_v], 1)
            keypoint_uv21 = keypoint_uv21

            # Modify camera intrinsics
            scale = tf.reshape(scale, [1, ])
            scale_matrix = tf.dynamic_stitch([[0], [1], [2],
                                              [3], [4], [5],
                                              [6], [7], [8]], [scale, [0.0], [0.0],
                                                               [0.0], scale, [0.0],
                                                               [0.0], [0.0], [1.0]])
            scale_matrix = tf.reshape(scale_matrix, [3, 3])

            crop_center_float = tf.cast(crop_center, tf.float32)
            trans1 = crop_center_float[0] * scale - crop_size // 2
            trans2 = crop_center_float[1] * scale - crop_size // 2
            trans1 = tf.reshape(trans1, [1, ])
            trans2 = tf.reshape(trans2, [1, ])
            trans_matrix = tf.dynamic_stitch([[0], [1], [2],
                                              [3], [4], [5],
                                              [6], [7], [8]], [[1.0], [0.0], -trans2,
                                                               [0.0], [1.0], -trans1,
                                                               [0.0], [0.0], [1.0]])
            trans_matrix = tf.reshape(trans_matrix, [3, 3])

            k = tf.matmul(trans_matrix, tf.matmul(scale_matrix, k))

        """ DEPENDENT DATA ITEMS: Scoremap from the SUBSET of 21 keypoints"""
        # create scoremaps from the subset of 2D annoataion
        keypoint_hw21 = tf.stack([keypoint_uv21[:, 1], keypoint_uv21[:, 0]], -1)

        scoremap_size = image_size

        if hand_crop:
            scoremap_size = (crop_size, crop_size)

        scoremap = BaseDataset.create_multiple_gaussian_map(keypoint_hw21,
                                                             scoremap_size,
                                                             sigma,
                                                             valid_vec=keypoint_vis21)

        if scoremap_dropout:
            scoremap = tf.nn.dropout(scoremap, scoremap_dropout_prob,
                                     noise_shape=[1, 1, 21])
            scoremap *= scoremap_dropout_prob


        return image_crop, keypoint_xyz21, keypoint_uv21, scoremap, keypoint_vis21, k, num_px_left_hand, num_px_right_hand
"""
dataset_RHD = RHD()
with tf.Session() as sess:

    for i in tqdm(range(dataset_RHD.example_num)):
        image, keypoint_xyz, keypoint_uv, scoremap, keypoint_vis, k, num_px_left_hand, num_px_right_hand\
            = sess.run(dataset_RHD.get_batch_data)
        #print(i)
        #print(dataset_RHD.dataset.output_shapes)
        #print(dataset_RHD.dataset.output_types)
        RHD.visualize_data(image[0], keypoint_xyz[0], keypoint_uv[0], keypoint_vis[0], k[0], num_px_left_hand[0], num_px_right_hand[0], scoremap[0])
"""