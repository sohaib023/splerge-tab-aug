import os
import pickle
import string
import random

import cv2
import torch
import numpy as np
from PIL import Image
from torchvision import transforms
import torchvision.transforms.functional as TF

from termcolor import cprint

from libs.utils import resize_image
from libs.utils import normalize_numpy_image

from truthpy import Document
# from augmentation.augmentor import Augmentor, apply_action
from augmentation.generate_prob_samples import ProbBasedAugmentor


class SplitTableDataset(torch.utils.data.Dataset):
    def __init__(
        self,
        root,
        fix_resize=False,
        augment=False,
        classical_augment=False,
    ):

        self.fix_resize = fix_resize
        self.train_images_path = os.path.join(root, "images")
        self.train_labels_path = os.path.join(root, "gt")
        self.train_ocr_path    = os.path.join(root, "ocr")

        self.augment = augment

        # cprint(self.root, "yellow")
        # cprint(self.train_images_path, "yellow")
        # cprint(self.train_labels_path, "yellow")

        self.filenames = list(
            sorted(os.listdir(self.train_images_path))
        )
        self.filenames = list(map(lambda name: os.path.basename(name).rsplit('.', 1)[0], self.filenames))

        self.augmentor = ProbBasedAugmentor(
            distribution_filepath="distributions/icdar_metadata.pkl", 
            nodes_filepath="distributions/icdar_nodes_3.pkl"
        )

        self.classical_augment = classical_augment
        if self.classical_augment:
            self.classical_transform = transforms.RandomApply([
                transforms.ColorJitter(brightness=(0.7, 1.3), contrast=(0.7,2.5), saturation=(0,2), hue=0.5),
            ], p=0.4)

    def read_record(self, idx):
        filename = self.filenames[idx]
        image_file = os.path.join(self.train_images_path, filename + ".png")
        xml_file = os.path.join(self.train_labels_path, filename + ".xml")
        ocr_file = os.path.join(self.train_ocr_path, filename + ".pkl")

        img = cv2.imread(image_file)

        with open(ocr_file, "rb") as f:
            ocr = pickle.load(f)
        doc = Document(xml_file)
        assert len(doc.tables) == 1
        table = doc.tables[0]

        if self.augment is True:
            # cv2.imshow("before aug", img)
            table, img, ocr = self.augmentor.apply_augmentation(filename, table, img.copy(), ocr.copy())
            # cv2.imshow("after  aug", img)
            # cv2.waitKey(0)

        ocr_mask = np.zeros_like(img)
        for word in ocr:
            txt = word[1].translate(str.maketrans("", "", string.punctuation))
            if len(txt.strip()) > 0:
                cv2.rectangle(ocr_mask, (word[2], word[3]), (word[4], word[5]), 255, -1)
        ocr_mask_row = ocr_mask.copy()
        # cv2.imshow("mask", ocr_mask)

        columns = [1] + [col.x1 for col in table.gtCols] + [img.shape[1] - 1]
        rows = [1] + [row.y1 for row in table.gtRows] + [img.shape[0] - 1]

        for row in table.gtCells:
            for cell in row:
                x0, y0, x1, y1 = tuple(cell)
                if cell.startRow != cell.endRow:
                    cv2.rectangle(ocr_mask_row, (x0, y0), (x1, y1), 0, -1)
                if cell.startCol != cell.endCol:
                    cv2.rectangle(ocr_mask, (x0, y0), (x1, y1), 0, -1)

        col_gt_mask = np.zeros_like(img[0, :, 0])
        row_gt_mask = np.zeros_like(img[:, 0, 0])

        non_zero_rows = np.append(
            np.where(np.count_nonzero(ocr_mask_row, axis=1) != 0)[0],
            [-1, img.shape[0]],
        )
        non_zero_cols = np.append(
            np.where(np.count_nonzero(ocr_mask, axis=0) != 0)[0],
            [-1, img.shape[1]],
        )
        zero_rows = np.where(np.count_nonzero(ocr_mask_row, axis=1) == 0)[0]
        zero_cols = np.where(np.count_nonzero(ocr_mask, axis=0) == 0)[0]

        for col in columns:
            if col == 0 or col == img.shape[1]:
                continue
            diff = non_zero_cols - col
            left = min(-diff[diff < 0]) - 1
            right = min(diff[diff > 0])

            # Re-align the seperators passing through an ocr bounding box
            try:
                if left == 0 and right == 1:
                    if col == 1 or col == img.shape[1] - 1:
                        continue
                    diff_zeros = zero_cols - col
                    left_align = min(-diff_zeros[diff_zeros < 0])
                    right_align = min(diff_zeros[diff_zeros > 0])

                    if min(left_align, right_align) < 20:
                        if left_align < right_align:
                            col -= left_align
                        else:
                            col += right_align

                        diff = non_zero_cols - col
                        left = min(-diff[diff < 0]) - 1
                        right = min(diff[diff > 0])
            except Exception as e:
                pass

            col_gt_mask[col - left : col + right] = 255

        for row in rows:
            if row == 0 or row == img.shape[0]:
                continue
            diff = non_zero_rows - row
            above = min(-diff[diff < 0]) - 1
            below = min(diff[diff > 0])

            # Re-align the seperators passing through an ocr bounding box
            try:
                if above == 0 and below == 1:
                    if row == 1 or row == img.shape[0] - 1:
                        continue
                    diff_zeros = zero_rows - row
                    above_align = min(-diff_zeros[diff_zeros < 0])
                    below_align = min(diff_zeros[diff_zeros > 0])

                    if min(above_align, below_align) < 20:
                        if above_align < below_align:
                            row -= above_align
                        else:
                            row += below_align

                        diff = non_zero_rows - row
                        above = min(-diff[diff < 0]) - 1
                        below = min(diff[diff > 0])
            except Exception as e:
                pass

            row_gt_mask[row - above : row + below] = 255
        return img, row_gt_mask, col_gt_mask

    def __getitem__(self, idx):
        image, row_label, col_label = self.read_record(idx)

        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

        # cv2.imshow("before", image.astype(np.uint8))
        if self.classical_augment:
            # Cropping
            if random.random() < 0.4:
                # print("cropping")
                h, w = image.shape[:2]
                crop_w = random.randint(int(w * 0.6), w)
                x = random.randint(0, (w - crop_w))

                crop_h = random.randint(int(h * 0.6), h)
                y = random.randint(0, (h - crop_h))

                image = image[y: y+crop_h, x: x + crop_w, :]
                row_label = row_label[y: y+crop_h]
                col_label = col_label[x: x+crop_w]
            image = Image.fromarray(image)
            image = np.array(self.classical_transform(image))

            # for i in range(250):
            #     if not os.path.exists("debug/{}-classical.png".format(i)):
            #         cv2.imwrite("debug/{}-classical.png".format(i), image)
            #         break
        # cv2.imshow("after", image.astype(np.uint8))
        # cv2.waitKey(0)

        H, W, C = image.shape
        image = image.astype(np.float32)
        image = resize_image(image, fix_resize=self.fix_resize)

        # image_write = image.copy()
        # image_write = (image_write.transpose((1, 2, 0))*255).astype(np.uint8)
        # cv2.imwrite("debug/{}_image.png".format(self.filenames[idx]), image_write)

        o_H, o_W, _ = image.shape
        scale = o_H / H

        row_label = cv2.resize(row_label[np.newaxis, :], (o_H, 1), interpolation=cv2.INTER_NEAREST)
        col_label = cv2.resize(col_label[np.newaxis, :], (o_W, 1), interpolation=cv2.INTER_NEAREST)

        # image_write[row_label[0] == 255, :, :] = [255, 0, 255]
        # image_write[:, col_label[0] == 255, :] = [255, 0, 255]
        # cv2.imshow("labels.png", image_write)
        # cv2.waitKey(0)

        row_label[row_label > 0] = 1
        col_label[col_label > 0] = 1

        row_label = torch.tensor(row_label[0])
        col_label = torch.tensor(col_label[0])

        target = [row_label, col_label]

        image = image.transpose((2, 0, 1))
        image = normalize_numpy_image(image)

        # print(image.shape, row_label.shape, col_label.shape)
        return image, target, self.filenames[idx], W, H

    def __len__(self):
        return len(self.filenames)


class MergeTableDataset(torch.utils.data.Dataset):
    def __init__(self, root, train_features_path, train_labels_path, transform=None):
        self.root = root
        self.train_features_path = train_features_path
        self.train_labels_path = train_labels_path
        self.transforms = transform

        self.feature_paths_list = list(
            sorted(os.listdir(os.path.join(self.root, self.train_features_path)))
        )

    def __getitem__(self, idx):
        feature_path = os.path.join(
            self.root, self.train_features_path, self.feature_paths_list[idx]
        )
        file_name = self.feature_paths_list[idx][:-4]
        target_path = os.path.join(self.root, self.train_labels_path, file_name)

        with open(feature_path, "rb") as f:
            input_feature = pickle.load(feature_path)

        with open(target_path, "rb") as f:
            target = pickle.load(target_path)

        # if self.transforms is not None:
        #     image, target = self.transforms(image, target)

        return input_feature, target, feature_path

    def __len__(self):
        return len(self.img_paths)

