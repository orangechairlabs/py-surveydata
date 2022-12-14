#  Copyright (c) 2022 Orange Chair Labs LLC
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

from .storagesystem import StorageSystem
from .filestorage import FileStorage
from .s3storage import S3Storage
from .googlecloudstorage import GoogleCloudStorage
from .azureblobstorage import AzureBlobStorage
from .dynamodbstorage import DynamoDBStorage
from .surveyplatform import SurveyPlatform
from .surveyctoplatform import SurveyCTOPlatform
from .surveyctoexportstorage import SurveyCTOExportStorage
from .odkexportstorage import ODKExportStorage
from .odkplatform import ODKPlatform

# report our current version, as installed
from importlib_metadata import version
try:
    __version__ = version("surveydata")
except:
    # (ignore exceptions when developing and testing locally)
    pass
