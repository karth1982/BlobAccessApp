using System;
using System.IO;
using System.Threading.Tasks;
using Microsoft.Azure.Storage;
using Microsoft.Azure.Storage.Blob;

namespace BlobAccessApp.Common
{
    public class BlobAccess
    {

        public static CloudBlockBlob GetBlobReference(string containerNameString,
                                                        string blobNameString)
        {

            CloudStorageAccount cloudStorageAccount = null;
            var connectionString = Environment.GetEnvironmentVariable("AzureWebJobsStorage");
            var couldParse = CloudStorageAccount.TryParse(connectionString, out cloudStorageAccount);
            if (couldParse == false)
                return null;

            var cloudBlobClient = cloudStorageAccount.CreateCloudBlobClient();
            var blobContainerReference = cloudBlobClient.GetContainerReference(containerNameString);
            var blobReference = blobContainerReference.GetBlockBlobReference(blobNameString);
            return blobReference;

        }


    }
}
