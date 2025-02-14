import sys, os
import torch
import random

import torch.nn as nn
import torch.nn.functional as F

class Classification(nn.Module):

	def __init__(self, emb_size, num_classes):
		super(Classification, self).__init__()

		#self.weight = nn.Parameter(torch.FloatTensor(emb_size, num_classes))
		self.layer = nn.Sequential(
								nn.Linear(emb_size, num_classes)	  
								#nn.ReLU()
							)
		self.init_params()

	def init_params(self):
		for param in self.parameters():
			if len(param.size()) == 2:
				nn.init.xavier_uniform_(param)

	def forward(self, embeds):
		logists = torch.log_softmax(self.layer(embeds), 1)
		return logists

# class Classification(nn.Module):

# 	def __init__(self, emb_size, num_classes):
# 		super(Classification, self).__init__()

# 		self.weight = nn.Parameter(torch.FloatTensor(emb_size, num_classes))
# 		self.init_params()

# 	def init_params(self):
# 		for param in self.parameters():
# 			nn.init.xavier_uniform_(param)

# 	def forward(self, embeds):
# 		logists = torch.log_softmax(torch.mm(embeds,self.weight), 1)
# 		return logists

class UnsupervisedLoss(object):
	"""docstring for UnsupervisedLoss"""
	def __init__(self, adj_lists, train_nodes, device):
		super(UnsupervisedLoss, self).__init__()
		self.Q = 10
		self.N_WALKS = 6  # 正采样走的次数（也就是需要采集的正样本数量）
		self.WALK_LEN = 1  # 正采样走的距离，这里是1，确保是1阶邻域
		self.N_WALK_LEN = 5  # 负采样走的距离，为了避免采样到邻接，因此比正采样走的更加远, 这里使用5，代表采集5阶邻域以外的点为负样本
		self.MARGIN = 3
		self.adj_lists = adj_lists
		self.train_nodes = train_nodes
		self.device = device

		self.target_nodes = None
		self.positive_pairs = []
		self.negtive_pairs = []
		self.node_positive_pairs = {}
		self.node_negtive_pairs = {}
		self.unique_nodes_batch = []

	def get_loss_sage(self, embeddings, nodes):
		assert len(embeddings) == len(self.unique_nodes_batch)
		assert False not in [nodes[i]==self.unique_nodes_batch[i] for i in range(len(nodes))]
		node2index = {n:i for i,n in enumerate(self.unique_nodes_batch)}

		nodes_score = []
		assert len(self.node_positive_pairs) == len(self.node_negtive_pairs)
		for node in self.node_positive_pairs:
			pps = self.node_positive_pairs[node]
			nps = self.node_negtive_pairs[node]
			if len(pps) == 0 or len(nps) == 0:
				continue

			# Q * Exception(negative score)
			indexs = [list(x) for x in zip(*nps)]
			node_indexs = [node2index[x] for x in indexs[0]]
			neighb_indexs = [node2index[x] for x in indexs[1]]
			neg_score = F.cosine_similarity(embeddings[node_indexs], embeddings[neighb_indexs])
			neg_score = self.Q*torch.mean(torch.log(torch.sigmoid(-neg_score)), 0)
			#print(neg_score)

			# multiple positive score
			indexs = [list(x) for x in zip(*pps)]
			node_indexs = [node2index[x] for x in indexs[0]]
			neighb_indexs = [node2index[x] for x in indexs[1]]
			pos_score = F.cosine_similarity(embeddings[node_indexs], embeddings[neighb_indexs])
			pos_score = torch.log(torch.sigmoid(pos_score))
			#print(pos_score)

			nodes_score.append(torch.mean(- pos_score - neg_score).view(1,-1))
				
		loss = torch.mean(torch.cat(nodes_score, 0))
		
		return loss

	def get_loss_margin(self, embeddings, nodes):
		assert len(embeddings) == len(self.unique_nodes_batch)
		assert False not in [nodes[i]==self.unique_nodes_batch[i] for i in range(len(nodes))]
		node2index = {n:i for i,n in enumerate(self.unique_nodes_batch)}

		nodes_score = []
		assert len(self.node_positive_pairs) == len(self.node_negtive_pairs)
		for node in self.node_positive_pairs:
			pps = self.node_positive_pairs[node]
			nps = self.node_negtive_pairs[node]
			if len(pps) == 0 or len(nps) == 0:
				continue

			indexs = [list(x) for x in zip(*pps)]
			node_indexs = [node2index[x] for x in indexs[0]]
			neighb_indexs = [node2index[x] for x in indexs[1]]
			pos_score = F.cosine_similarity(embeddings[node_indexs], embeddings[neighb_indexs])
			pos_score, _ = torch.min(torch.log(torch.sigmoid(pos_score)), 0)

			indexs = [list(x) for x in zip(*nps)]
			node_indexs = [node2index[x] for x in indexs[0]]
			neighb_indexs = [node2index[x] for x in indexs[1]]
			neg_score = F.cosine_similarity(embeddings[node_indexs], embeddings[neighb_indexs])
			neg_score, _ = torch.max(torch.log(torch.sigmoid(neg_score)), 0)

			nodes_score.append(torch.max(torch.tensor(0.0).to(self.device), neg_score-pos_score+self.MARGIN).view(1,-1))
			# nodes_score.append((-pos_score - neg_score).view(1,-1))

		loss = torch.mean(torch.cat(nodes_score, 0),0)

		# loss = -torch.log(torch.sigmoid(pos_score))-4*torch.log(torch.sigmoid(-neg_score))
		
		return loss


	def extend_nodes(self, nodes, num_neg=6):
		"""
		parameters:
			nodes: 一批节点
			num_neg: 负采样数量
		"""
		self.positive_pairs = []
		self.node_positive_pairs = {}
		self.negtive_pairs = []
		self.node_negtive_pairs = {}

		self.target_nodes = nodes
		self.get_positive_nodes(nodes)
		# print(self.positive_pairs)
		self.get_negtive_nodes(nodes, num_neg)
		# print(self.negtive_pairs)
		self.unique_nodes_batch = list(set([i for x in self.positive_pairs for i in x]) | set([i for x in self.negtive_pairs for i in x]))
		assert set(self.target_nodes) < set(self.unique_nodes_batch)
		return self.unique_nodes_batch

	def get_positive_nodes(self, nodes):
		return self._run_random_walks(nodes)

	def get_negtive_nodes(self, nodes, num_neg):
		"""
		parameters:
			nodes: 一批节点
			num_neg: 想要的负样本个数
		"""
		for node in nodes:
			neighbors = set([node])  # 存储经过遍历的节点
			frontier = set([node])  # 存储每次遍历中新出现的节点
			for i in range(self.N_WALK_LEN):
				current = set()  #
				for outer in frontier:
					current |= self.adj_lists[int(outer)]  # 取当前遍历的节点的邻域节点
				frontier = current - neighbors  # 存储当前遍历节点的所有非邻域节点
				neighbors |= current  # 将当前点的邻域放入邻域集合中
			far_nodes = set(self.train_nodes) - neighbors  # 得到所有非领域节点
			neg_samples = random.sample(far_nodes, num_neg) if num_neg < len(far_nodes) else far_nodes  # 挑选5阶邻域之外的节点
			self.negtive_pairs.extend([(node, neg_node) for neg_node in neg_samples])
			self.node_negtive_pairs[node] = [(node, neg_node) for neg_node in neg_samples]
		return self.negtive_pairs

	def _run_random_walks(self, nodes):
		"""
		parameters:
			nodes: 一批节点
		"""
		for node in nodes:
			if len(self.adj_lists[int(node)]) == 0:  # 如果当前遍历的节点没有邻接节点，那么直接跳过
				continue
			cur_pairs = []
			for i in range(self.N_WALKS):
				curr_node = node
				for j in range(self.WALK_LEN):
					neighs = self.adj_lists[int(curr_node)]  # 读取当前节点的邻域节点
					next_node = random.choice(list(neighs))  # 从所有邻域节点中随机选择一个
					# self co-occurrences are useless
					if next_node != node and next_node in self.train_nodes:  # 如果没有选择到自己，并且选择节点处在训练集中
						self.positive_pairs.append((node,next_node))  # 记录这一对节点
						cur_pairs.append((node,next_node))
					curr_node = next_node  # 游走的起点转化成刚刚选择的节点

			self.node_positive_pairs[node] = cur_pairs
		return self.positive_pairs
		

class SageLayer(nn.Module):
	"""
	Encodes a node's using 'convolutional' GraphSage approach
	"""
	def __init__(self, input_size, out_size, gcn=False): 
		super(SageLayer, self).__init__()

		self.input_size = input_size
		self.out_size = out_size


		self.gcn = gcn
		self.weight = nn.Parameter(torch.FloatTensor(out_size, self.input_size if self.gcn else 2 * self.input_size))

		self.init_params()

	def init_params(self):
		for param in self.parameters():
			nn.init.xavier_uniform_(param)

	def forward(self, self_feats, aggregate_feats, neighs=None):
		"""
		Generates embeddings for a batch of nodes.

		nodes	 -- list of nodes
		"""
		if not self.gcn:
			combined = torch.cat([self_feats, aggregate_feats], dim=1)
		else:
			combined = aggregate_feats
		combined = F.relu(self.weight.mm(combined.t())).t()
		return combined

class GraphSage(nn.Module):
	"""docstring for GraphSage"""
	def __init__(self, num_layers, input_size, out_size, raw_features, adj_lists, device, gcn=False, agg_func='MEAN'):
		"""
		parameters:
			num_layers: 网络层数
			input_size: 节点向量特征维度
			out_size: 嵌入完之后的节点的表示维度
			raw_features: 所有节点输入向量表示
			adj_lists: 图的邻接矩阵
			device: cpu|gpu
			gcn: 是否使用GCN
			agg_func: 聚合函数类型
		"""
		super(GraphSage, self).__init__()

		self.input_size = input_size
		self.out_size = out_size
		self.num_layers = num_layers
		self.gcn = gcn
		self.device = device
		self.agg_func = agg_func

		self.raw_features = raw_features
		self.adj_lists = adj_lists

		for index in range(1, num_layers+1):
			layer_size = out_size if index != 1 else input_size
			setattr(self, 'sage_layer'+str(index), SageLayer(layer_size, out_size, gcn=self.gcn))

	def forward(self, nodes_batch):
		"""
		Generates embeddings for a batch of nodes.
		nodes_batch	-- batch of nodes to learn the embeddings
		"""
		lower_layer_nodes = list(nodes_batch)
		nodes_batch_layers = [(lower_layer_nodes,)]
		# self.dc.logger.info('get_unique_neighs.')
		# 每一层都是 随机 选取的邻域
		for i in range(self.num_layers):
			lower_samp_neighs, lower_layer_nodes_dict, lower_layer_nodes= self._get_unique_neighs_list(lower_layer_nodes)
			nodes_batch_layers.insert(0, (lower_layer_nodes, lower_samp_neighs, lower_layer_nodes_dict))

		assert len(nodes_batch_layers) == self.num_layers + 1

		pre_hidden_embs = self.raw_features
		for index in range(1, self.num_layers+1):
			nb = nodes_batch_layers[index][0]   # 这批节点及其所有邻域节点
			pre_neighs = nodes_batch_layers[index-1]  # 上一波节点的信息
			# self.dc.logger.info('aggregate_feats.')
			aggregate_feats = self.aggregate(nb, pre_hidden_embs, pre_neighs)
			sage_layer = getattr(self, 'sage_layer'+str(index))
			if index > 1:
				nb = self._nodes_map(nb, pre_hidden_embs, pre_neighs)
			# self.dc.logger.info('sage_layer.')
			cur_hidden_embs = sage_layer(self_feats=pre_hidden_embs[nb],
										aggregate_feats=aggregate_feats)
			pre_hidden_embs = cur_hidden_embs

		return pre_hidden_embs

	def _nodes_map(self, nodes, hidden_embs, neighs):
		layer_nodes, samp_neighs, layer_nodes_dict = neighs
		assert len(samp_neighs) == len(nodes)
		index = [layer_nodes_dict[x] for x in nodes]
		return index

	def _get_unique_neighs_list(self, nodes, num_sample=10):
		"""
		parameters:
			nodes: 这一批节点
			num_sample: 采样个数
		return:
			这批数据所有节点的邻域信息（包含自己）
			打乱后每个节点的索引和值
			所有邻域的一个大集合，最后转化成了列表
		"""
		_set = set
		to_neighs = [self.adj_lists[int(node)] for node in nodes]  # 存储这批节点的左右领域信息
		# 如果指定了采样个数，那么就随机选取指定个数条节点的领域信息
		if not num_sample is None:
			_sample = random.sample
			samp_neighs = [_set(_sample(to_neigh, num_sample)) if len(to_neigh) >= num_sample else to_neigh for to_neigh in to_neighs]
		else:
			samp_neighs = to_neighs
		samp_neighs = [samp_neigh | set([nodes[i]]) for i, samp_neigh in enumerate(samp_neighs)]  # 将节点自己也作为邻域添加进去
		_unique_nodes_list = list(set.union(*samp_neighs))  # 将所有邻域记录集合并起来，然后转化成一个列表
		i = list(range(len(_unique_nodes_list)))  # 创建列表中每个元素对应的索引
		unique_nodes = dict(list(zip(_unique_nodes_list, i)))
		return samp_neighs, unique_nodes, _unique_nodes_list

	def aggregate(self, nodes, pre_hidden_embs, pre_neighs, num_sample=10):
		"""
		parameters:
			nodes: 这一次需要聚合的节点及其邻域信息
			pre_hidden_embs:  上一次聚合得到的所有节点的嵌入表示
			pre_neighs:  上一波被聚合的节点信息
		"""
		unique_nodes_list, samp_neighs, unique_nodes = pre_neighs

		assert len(nodes) == len(samp_neighs)
		indicator = [(nodes[i] in samp_neighs[i]) for i in range(len(samp_neighs))]  # 检测这次需要聚合的节点是否都在上次聚合的信息中
		assert (False not in indicator)
		if not self.gcn:
			samp_neighs = [(samp_neighs[i]-set([nodes[i]])) for i in range(len(samp_neighs))]  # 如果没有使用GCN，那么就吧邻域中包含的需要训练的节点剔除
		# self.dc.logger.info('2')
		if len(pre_hidden_embs) == len(unique_nodes):
			embed_matrix = pre_hidden_embs
		else:
			embed_matrix = pre_hidden_embs[torch.LongTensor(unique_nodes_list)]  # 选择对应节点的嵌入表示
		# self.dc.logger.info('3')
		mask = torch.zeros(len(samp_neighs), len(unique_nodes))
		column_indices = [unique_nodes[n] for samp_neigh in samp_neighs for n in samp_neigh]
		row_indices = [i for i in range(len(samp_neighs)) for j in range(len(samp_neighs[i]))]
		mask[row_indices, column_indices] = 1
		# self.dc.logger.info('4')

		if self.agg_func == 'MEAN':
			num_neigh = mask.sum(1, keepdim=True)
			mask = mask.div(num_neigh).to(embed_matrix.device)
			aggregate_feats = mask.mm(embed_matrix)

		elif self.agg_func == 'MAX':
			# print(mask)
			indexs = [x.nonzero() for x in mask==1]
			aggregate_feats = []
			# self.dc.logger.info('5')
			for feat in [embed_matrix[x.squeeze()] for x in indexs]:
				if len(feat.size()) == 1:
					aggregate_feats.append(feat.view(1, -1))
				else:
					aggregate_feats.append(torch.max(feat,0)[0].view(1, -1))
			aggregate_feats = torch.cat(aggregate_feats, 0)

		# self.dc.logger.info('6')
		
		return aggregate_feats
