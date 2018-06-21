from pathlib import Path
import yaml
import sys
import importlib
import os
import hashlib
import logging
import urllib
import shutil
from itertools import chain

class Compression:
    @staticmethod
    def extension(definition):
        if not definition: 
            return ""
        if definition == "gzip":
            return ".gz"

        raise Exception("Not handled compression definition: %s" % definition)

class RegistryEntry:
    def __init__(self, registry, key):    
        self.key = key
        self.dicts = []
        _key = ""   
        for subkey in self.key.split("."):
            _key = "%s.%s" % (_key, subkey) if _key else subkey
            if _key in registry.content:
                self.dicts.insert(0, registry.content[_key])
        
    def get(self, key, default):
        for d in self.dicts:
            if key in d:
                return d[key]
        return default
        
    def __getitem__(self, key):
        for d in self.dicts:
            if key in d:
                return d[key]
        raise KeyError(key)


class Registry:
    def __init__(self, path):
        self.path = path
        if path.is_file():
            with open(path, "r") as fp:
                self.content = yaml.safe_load(fp)

    def __getitem__(self, key):
        return RegistryEntry(self, key)



class CachedFile():
    """Represents a downloaded file that has been cached"""
    def __init__(self, path, *paths):
        self.path = path
        self.paths = paths
    
    def discard(self):
        """Delete all cached files"""
        for p in chain([self.path], self.paths):
            try:
                p.unlink()
            except Exception as e:
                logging.warn("Could not delete cached file %s", p)

    def path(self):
        return self.path()


import progressbar

class DownloadReportHook:
    def __init__(self):
        self.pbar = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.pbar:
            self.pbar.__exit__(exc_type, exc_val, exc_tb)

    def __call__(self, block_num, block_size, total_size):
        if not self.pbar:
            self.pbar = progressbar.ProgressBar(maxval=total_size).__enter__()

        downloaded = block_num * block_size
        if downloaded < total_size:
            self.pbar.update(downloaded)
        
class Context:
    """
    Represents the application context
    """
    MAINDIR = Path("~/datasets").expanduser()

    """Main settings"""
    def __init__(self, path: Path = None):
        self._path = path or Context.MAINDIR
        self._dpath = Path(__file__).parents[1]
        self.registry = Registry(self._path.joinpath("registry.yaml"))
        self._repository = None

    @property
    def repositoriespath(self):
        """Directory containing repositories"""
        return self._path.joinpath("repositories")

    @property
    def datapath(self):
        return self._path.joinpath("data")

    @property
    def datasetspath(self):
        return self._path.joinpath("datasets")

    @property
    def webpath(self) -> Path:
        return self._path.joinpath("www")

    @property
    def cachepath(self) -> Path:
        return self._path.joinpath("cache")

    @property
    def mainrepository(self):
        from .data import Repository
        if not self._repository:
            self._repository = Repository(self, self._dpath)
        return self._repository

    def repositories(self):
        """Returns the repository"""
        from .data import Repository
        return [self.mainrepository]

    def repository(self, repositoryid):
        if repositoryid == "main":
            return self.mainrepository
                    
        return Repository(self, self.repositoriespath.joinpath(repositoryid))

    def datasets(self):
        """Returns an iterator over all files"""
        for repository in self.repositories():
            for dataset in repository:
                yield dataset

    def dataset(self, datasetid):
        from .data import Dataset
        return Dataset.find(self, datasetid)


    def download(self, url):
        """Downloads an URL"""
        hasher = hashlib.sha256(url.encode("utf-8"))

        self.cachepath.mkdir(exist_ok=True)
        path = self.cachepath.joinpath(hasher.hexdigest())
        urlpath = path.with_suffix(".url")
        dlpath = path.with_suffix(".dl")
    
        if urlpath.is_file():
            if urlpath.read_text() != url:
                # TODO: do something better
                raise Exception("Cached URL hash does not match. Clear cache to resolve")

        urlpath.write_text(url)
        if dlpath.is_file():
            logging.debug("Using cached file %s for %s", dlpath, url)
        else:

            logging.info("Downloading %s", url)
            tmppath = dlpath.with_suffix(".tmp")
            try:
                with DownloadReportHook() as reporthook:
                    urllib.request.urlretrieve(url, tmppath, reporthook)
                shutil.move(tmppath, dlpath)
            except:
                tmppath.unlink()
                raise


        return CachedFile(dlpath, urlpath)
        