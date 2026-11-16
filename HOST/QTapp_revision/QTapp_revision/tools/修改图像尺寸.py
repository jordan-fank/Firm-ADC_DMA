import numpy as np
import matplotlib.pyplot as plt

data = np.random.rand(60, 4000)


# plt.figure(figsize=(4, 12))   # 宽:高 = 1:3
# plt.imshow(data, cmap='gray', aspect='auto', interpolation='nearest')
# plt.axis('off')
# plt.show()


plt.figure(figsize=(12, 4))   # 宽:高 = 3:1
plt.imshow(data, cmap='gray', aspect='auto', interpolation='nearest')
plt.axis('off')
plt.show()