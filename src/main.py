"""
This file contains the model suite training class object allowing full control
over loading, training, and infering a specific model.
    @author:         Christoph S. Metzner
    @date:           06/17/2024
    @last modified:  07/01/2024
"""


# Load libaries
# built-in
import os
import sys
import csv
import json
import random
import yaml
from typing import Dict, List, Union
from datetime import datetime
import time
import warnings
warnings.filterwarnings('ignore')

# installed
import torch
from torch.utils.data import DataLoader
import numpy as np
import pandas as pd

# custom
from models import CNN
from command_line_args import create_args_parser
from training import Trainer
from dataloaders import GenericDataloader
from utils import compute_performance_metrics

# Set path to root project
try:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
except NameError:
    root = os.path.dirname(os.getcwd())
sys.path.append(root)


class ModelSuite:
    def __init__(
        self,
        experiment_name: str,
        model_description: str,
        dataset: str,
        model_type: str,
        transformer_model: bool,
        paths_dict: Dict[str, str],
        seed: int,
        device: str
    ):

        self._experiment_name = experiment_name
        self._model_description = model_description
        self._dataset = dataset
        self._model_type = model_type
        self._transformer_model = transformer_model
        self._paths_dict = paths_dict
        self._seed = seed
        self._device = device

        # Global model identification
        self._model_name = ''
        self._time_model_init = datetime.now().strftime('%Y%m%d%H%M%S')

    def _fetch_data(
            self,
            dataset: str,
            doc_max_len: int,
            splits: List[str] = ['train', 'val', 'test']
    ):
        """
        Method that fetches the specified dataset.

        Parameters
        ----------
        dataset : str
            Variable indicating the type of dataset; function expects following
            naming convention for the split [dataset]_[split].parquet
        doc_max_len : str
            Variable indicating the maximum document length
        splits : List[str], default=['train', 'val', 'test']
            List containing the splits that are being pulled.

        Return
        ------
        Dict
            Dictionary containing the respective splits - keys are splits

        """

        def pad_sequence(x: np.array, doc_max_len: int) -> np.array:
            """
            This function is padding the integer-indexed sequences. For
            huggingface transformer models the padding token has index 1.

            Parameters
            ----------
            x : np.array
                Integer-indexed text sequence
            doc_max_len : int
                Maximal document length

            Return
            ------
            np.array
                Padded (trimmed and padded) integer-indexed text sequences
            """
            if self._transformer_model:
                array = np.ones(doc_max_len)
            else:
                array = np.zeros(doc_max_len)
            x = x[:doc_max_len]   # trims sequences longer than max doc length
            doc_len = len(x)
            array[:doc_len] = x
            return array

        def generate_att_mask(x: np.array, doc_max_len: int) -> np.array:
            """
            This function creates an attention mask.
                1 for tokens that are NOT masked
                0 for tokens that are masked
            Parameters
            ----------
            x : np.array
                Integer-indexed text sequence
            doc_max_len : int
                Maximal document length

            Return
            ------
            np.array
                Array containing the attention masks
            """
            att_mask = np.zeros(doc_max_len)
            x = x[:doc_max_len]
            doc_len = len(x)
            att_mask_ones = np.ones(doc_len)
            att_mask[:doc_len] = att_mask_ones
            return att_mask

        data = {}
        for split in splits:
            if split not in data:
                data[split] = {}
            df_split = pd.read_parquet(
                os.path.join(
                    self._paths_dict['path_dataset'],
                    f'{self._dataset}_{split}.parquet'
                )
            )

            df_split['X_padded'] = df_split.apply(
                lambda x: pad_sequence(
                    x=np.array(x.X),
                    doc_max_len=doc_max_len
                ),
                axis=1
            )
            df_split['X_att_mask'] = df_split.apply(
                lambda x: generate_att_mask(
                    x=np.array(x.X), doc_max_len=doc_max_len),
                axis=1
            )

            x = df_split.loc[:, 'X'].tolist()
            y = df_split.loc[:, 'Y'].tolist()

            data[split]['X'] = x
            data[split]['Y'] = y
        return data

    def _fit_model_config(
            self,
            config_args: Dict[str, Union[str, bool, float]]
    ):
        with open(os.path.join(os.getcwd(), 'models_config.yml'), 'r') as f:
            model_config = yaml.safe_load(stream=f)

        with open(os.path.join(
            self._paths_dict['path_dataset'],
            f'{self._dataset}_id2label.json'
        ), 'r') as f:
            id2label = json.load(f)

        # Update train_kwargs
        if config_args['doc_max_len'] is not None:
            model_config['train_kwargs']['doc_max_len'] = config_args['doc_max_len']
        if config_args['batch_size'] is not None:
            model_config['train_kwargs']['batch_size'] = config_args['batch_size']
        if config_args['epochs'] is not None:
            model_config['train_kwargs']['epochs'] = config_args['epochs']
        if config_args['patience'] is not None:
            model_config['train_kwargs']['patience'] = config_args['patience']
        if config_args['learning_rate'] is not None:
            model_config['train_kwargs']['learning_rate'] = config_args['learning_rate']

        # Update model_kwargs
        model_config['model_kwargs'][self._model_type]['num_classes'] = id2label['num_labels']

        if config_args['hidden_size'] is not None:
            model_config['model_kwargs'][self._model_type]['hidden_size'] = config_args['hidden_size']
        if config_args['dropout_prob'] is not None:
            model_config['model_kwargs'][self._model_type]['dropout_prob'] = config_args['dropout_prob']
        if config_args['logits_mechanism'] is not None:
            model_config['model_kwargs'][self._model_type]['logits_mechanism'] = config_args['logits_mechanism']

        if not self._transformer_model:
            model_config['model_kwargs'][self._model_type]['vocab_size'] = id2label['vocab_size']
            if config_args['word_embedding_dim'] is not None:
                model_config['model_kwargs'][self._model_type]['word_embedding_dim'] = config_args['word_embedding_dim']
            # In the future we can add pretrained word embedding matrix here

        # Creeate a model_suite_kwargs
        model_config['model_suite'] = {}
        model_config['model_suite']['experiment_name'] = self._experiment_name
        model_config['model_suite']['model_description'] = self._model_description
        model_config['model_suite']['dataset'] = self._dataset
        model_config['model_suite']['model_type'] = self._model_type
        model_config['model_suite']['seed'] = self._seed
        model_config['model_suite']['time_model_init'] = self._time_model_init
        return model_config

    def _init_model(self, model_kwargs: Dict):
        if self._model_type == 'CNN':
            model = CNN(**model_kwargs, device=self._device)
        else:
            raise Exception('Invalid model type!')

        return model

    def _init_trainer(
        self,
        model,
        train_kwargs: Dict,
        debugging: bool = False,
        checkpoint: Dict = None,
    ):
        model.to(self._device)

        trainer = Trainer(
            model=model,
            model_type=self._model_type,
            model_name=self._model_name,
            train_kwargs=train_kwargs,
            paths_dict=self._paths_dict,
            debugging=debugging,
            checkpoint=checkpoint,
            ddp_training=False,
            device=self._device
        )
        return trainer

    def _train_model(
        self,
        trainer,
        train_kwargs,
        train_data,
        val_data
    ):
        dataloader = GenericDataloader

        train_dataset = dataloader(X=train_data['X'], Y=train_data['Y'])
        val_dataset = dataloader(X=val_data['X'], Y=val_data['Y'])

        train_loader = DataLoader(
            dataset=train_dataset,
            batch_size=train_kwargs['batch_size'],
            num_workers=2,
            pin_memory=True,
            prefetch_factor=2,
            shuffle=True
        )

        val_loader = DataLoader(
            dataset=val_dataset,
            batch_size=train_kwargs['batch_size'],
            num_workers=2,
            pin_memory=True,
            prefetch_factor=2,
            shuffle=False
        )

        start_time_training = time.time()
        trainer.training(train_loader=train_loader, val_loader=val_loader)
        end_time_training = time.time()
        print(f'Training time: {end_time_training - start_time_training}', flush=True)
        # Here add delete checkpointing
        # Training complete - deleting last checkpoint
        if os.path.exists(os.path.join(
                self._paths_dict['path_models'],
                f'{self._model_name}_checkpoint.tar')
        ):
            print('Training complete - deleting last training checkpoint!')
            os.remove(os.path.join(
                self._paths_dict['path_models'],
                f'{self._model_name}_checkpoint.tar'
                )
            )

    def _eval_model(
        self,
        trainer,
        train_kwargs,
        inference_data
    ):
        dataloader = GenericDataloader

        inference_dataset = dataloader(X=inference_data['X'], Y=inference_data['Y'])

        inference_loader = DataLoader(
            dataset=inference_dataset,
            batch_size=train_kwargs['batch_size'],
            num_workers=2,
            pin_memory=True,
            prefetch_factor=2,
            shuffle=False
        )

        start_time_inferring = time.time()
        scores = trainer._predict(inf_loader=inference_loader)
        end_time_inferring = time.time()
        print(f'Inference time: {end_time_inferring - start_time_inferring}', flush=True)
        return scores

    def store_scores(
        self,
        metrics,
        model_config,
        inference_data
    ):
        # Creating dataframe with model scores from dict
        d = {
            # Model Suite parameters
            'dataset': [model_config['model_suite']['dataset']],
            'split': [inference_data],
            'experiment_name': [model_config['model_suite']['experiment_name']],
            'model_description': [model_config['model_suite']['model_description']],
            'model_type': [model_config['model_suite']['model_type']],
            'seed': [model_config['model_suite']['seed']],
            'model_time_init': [model_config['model_suite']['time_model_init']],
            # train arguments
            'doc_max_len': [model_config['train_kwargs']['doc_max_len']],
            'batch_size': [model_config['train_kwargs']['batch_size']],
            'epochs': [model_config['train_kwargs']['epochs']],
            'patience': [model_config['train_kwargs']['patience']],
            'learning_rate': [model_config['train_kwargs']['learning_rate']],
            'frequent_validation': [model_config['train_kwargs']['frequent_validation']],
            'n_steps': [model_config['train_kwargs']['n_steps']],
            # model specific arguments
            'hidden_size': [model_config['model_kwargs'][self._model_type]['hidden_size']],
            'dropout_prob': [model_config['model_kwargs'][self._model_type]['dropout_prob']],
            'logits_mechanism': [model_config['model_kwargs'][self._model_type]['logits_mechanism']],
            # Scores
            'f1_macro': [metrics['f1_macro']],
            'f1_micro': [metrics['f1_micro']],
            'accuracy': [metrics['accuracy']]
        }

        df = pd.DataFrame.from_dict(d) #columns=headers, data=values)

        if os.path.exists(os.path.join(self._paths_dict['path_results'], 'scores.xlsx')):
            df_excel = pd.read_excel(os.path.join(self._paths_dict['path_results'], 'scores.xlsx'))
            result = pd.concat([df_excel, df], ignore_index=True)
            result.to_excel(os.path.join(self._paths_dict['path_results'], 'scores.xlsx'), index=False)
        else:
            df.to_excel(os.path.join(self._paths_dict['path_results'], 'scores.xlsx'), header=True)

    def _update_model_suite_attributes(self, model_config: Dict):
        self._experiment_name = model_config['model_suite']['experiment_name']
        self._model_description = model_config['model_suite']['model_description']
        self._dataset = model_config['model_suite']['dataset']
        self._model_type = model_config['model_suite']['model_type']
        self._seed = model_config['model_suite']['seed']
        self._time_model_init = model_config['model_suite']['time_model_init']
        self._model_name = model_config['model_suite']['model_name']

    def eval_model(self, path_trained_model, debugging, inference_data):
        # Get model name from provided path
        model_name = path_trained_model.split('/')[-1].split('.')[0]
        # Retrieve model_config of trained model using path of path_trained_model
        with open(os.path.join('/'.join(path_trained_model.split('/')[:-1]), f'models_config_{model_name}.json'), 'r') as f:
            model_config = json.load(f)

        self._update_model_suite_attributes(model_config)

        model = self._init_model(model_kwargs=model_config['model_kwargs'][self._model_type])
        model.load_state_dict(torch.load(os.path.join(self._paths_dict['path_models'], f'{model_name}.pt'), map_location=torch.device(self._device)))
        model.to(self._device)

        trainer = self._init_trainer(
            model=model,
            train_kwargs=model_config['train_kwargs'],
            debugging=debugging,
        )

        data = self._fetch_data(dataset=self._dataset, doc_max_len=model_config['train_kwargs']['doc_max_len'])

        scores = self._eval_model(
            trainer=trainer,
            train_kwargs=model_config['train_kwargs'],
            inference_data=data[inference_data]
        )

        metrics = compute_performance_metrics(
            y_trues=scores['y_trues'],
            y_preds=scores['y_preds'],
            num_classes=model_config['model_kwargs'][self._model_type]['num_classes']
        )

        for metric, score in metrics.items():
            if metric != 'f1_ind':
                print(f'Metric {metric}: {score:.3f}', flush=True)
            else:
                print(f'Metric {metric}: {score}', flush=True)
        return scores, metrics, model_config

    def train_model(
        self,
        from_checkpoint: str,
        command_line_args: Dict,
        debugging: bool,
        inference_data: str,
        eval_model: bool
    ):
        if from_checkpoint is None:

            checkpoint = None
            # Fit model configuration file
            model_config = self._fit_model_config(command_line_args)
            model_name = '_'.join([str(value) for value in model_config['model_suite'].values()])
            print(f'The name of your model is: {model_name}', flush=True)

            model_config['model_suite']['model_name'] = model_name
            self._model_name = model_name
            # Save model_config file specific to your current new model
            print(f'models_config_{model_name}.json', flush=True)
            with open(os.path.join(self._paths_dict['path_models'], f'models_config_{model_name}.json'), 'w') as f:
                json.dump(model_config, f)

            model = self._init_model(model_kwargs=model_config['model_kwargs'][self._model_type])

        else:
            model_checkpoint = from_checkpoint.split('/')[-1].split('.')[0]
            model_name = model_checkpoint.split('_checkpoint')[0]

            # Retrieve model_config of trained model using path of path_trained_model
            with open(os.path.join('/'.join(from_checkpoint.split('/')[:-1]), f'models_config_{model_name}.json'), 'r') as f:
                model_config = json.load(f)

            self._update_model_suite_attributes(model_config)

            model = self._init_model(model_kwargs=model_config['model_kwargs'][self._model_type])
            checkpoint = torch.load(from_checkpoint, map_location=torch.device(self._device))

        trainer = self._init_trainer(
            model=model,
            train_kwargs=model_config['train_kwargs'],
            checkpoint=checkpoint,
            debugging=debugging,
        )

        # Fetch data
        data = self._fetch_data(dataset=self._dataset, doc_max_len=model_config['train_kwargs']['doc_max_len'])

        self._train_model(
            trainer=trainer,
            train_data=data['train'],
            val_data=data['val'],
            train_kwargs=model_config['train_kwargs']
        )

        if eval_model:
            # Load best performing model
            model.load_state_dict(torch.load(os.path.join(self._paths_dict['path_models'], f'{self._model_name}.pt'), map_location=torch.device(self._device)))
            model.to(self._device)
            trainer._model = model

            scores = self._eval_model(
                trainer=trainer,
                train_kwargs=model_config['train_kwargs'],
                inference_data=data[inference_data]
            )

            metrics = compute_performance_metrics(
                y_trues=scores['y_trues'],
                y_preds=scores['y_preds'],
                num_classes=model_config['model_kwargs'][self._model_type]['num_classes']
            )
            for metric, score in metrics.items():
                if metric != 'f1_ind':
                    print(f'Metric {metric}: {score:.3f}', flush=True)
                else:
                    print(f'Metric {metric}: {score}', flush=True)
            return scores, metrics, model_config
        return 0, 0


def main():
    # Setup command line arguments
    parser = create_args_parser()
    args = parser.parse_args()

    # Set seed for reproducibility
    np.random.seed(args.seed)
    random.seed(args.seed)
    torch.manual_seed(args.seed)

    # Check that MPS is available
    if torch.cuda.is_available():
        device = torch.device('cuda')
    elif torch.backends.mps.is_available():
        device = torch.device('mps')
    else:
        device = torch.device('cpu')

    print(f'Model is running on device: {device}')
    # Create dictionary with paths for controlling data flows
    paths_dict = {
        'path_root': root,
        'path_dataset': os.path.join(root, 'data', f'{args.dataset}_dataset'),
        'path_models': os.path.join(root, 'models/'),
        'path_results': os.path.join(root, 'results/'),
    }
    # Create directories if they do not exist
    if not os.path.exists(os.path.dirname(paths_dict['path_models'])):
        os.makedirs(os.path.dirname(paths_dict['path_models']))
    if not os.path.exists(os.path.dirname(paths_dict['path_results'])):
        os.makedirs(os.path.dirname(paths_dict['path_results']))

    transformer_model = False
    if args.model_type in ['CLF']:
        transformer_model = True

    # Initialize ModelSuite class
    model_suite = ModelSuite(
        experiment_name=args.experiment_name,
        model_description=args.model_description,
        paths_dict=paths_dict,
        model_type=args.model_type,
        transformer_model=transformer_model,
        dataset=args.dataset,
        seed=args.seed,
        device=device,
    )

    # Fit model configuration file
    command_line_args = {
        'experiment_name': args.experiment_name,
        'model_description': args.model_description,
        'model_type': args.model_type,
        'dataset': args.dataset,
        'seed': args.seed,
        # train_kwargs
        'doc_max_len': args.doc_max_len,
        'batch_size': args.batch_size,
        'epochs': args.epochs,
        'patience': args.patience,
        'learning_rate': args.learning_rate,
        'word_embedding_dim': args.word_embedding_dim,
        # model_kwargs
        'hidden_size': args.hidden_size,
        'dropout_prob': args.dropout_prob,
        'logits_mechanism': args.logits_mechanism,
    }

    from_checkpoint = None
    if args.from_checkpoint is not None:
        from_checkpoint = args.from_checkpoint

    if args.train_model:
        scores, metrics, model_config = model_suite.train_model(
            from_checkpoint=from_checkpoint,
            command_line_args=command_line_args,
            debugging=args.debugging,
            inference_data=args.inference_data,
            eval_model=args.eval_model
        )
        if args.eval_model:
            if args.store_scores:
                pass
            if args.store_performance_scores:
                model_suite.store_scores(
                    metrics,
                    model_config,
                    args.inference_data
                )
    else:
        if args.path_trained_model is None:
            raise ValueError("Provide absolute path to trained model\
                             utilize args.path_trained_model!")

        scores, metrics, model_config = model_suite.eval_model(
            path_trained_model=args.path_trained_model,
            debugging=args.debugging,
            inference_data=args.inference_data
        )

        if args.store_scores:
            # call function store scores
            pass

        if args.store_performance_scores:
            model_suite.store_scores(
                metrics,
                model_config,
                args.inference_data
            )


if __name__ == "__main__":
    main()
