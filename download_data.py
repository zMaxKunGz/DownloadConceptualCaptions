import pandas as pd
import numpy as np
import requests
import zlib
import os
import shelve
import magic #pip install python-magic
from multiprocessing import Pool
from tqdm import tqdm

headers = {
    #'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/71.0.3578.98 Safari/537.36',
    'User-Agent':'Googlebot-Image/1.0', # Pretend to be googlebot
    'X-Forwarded-For': '64.18.15.200'
}

def _df_split_apply(tup_arg):
    split_ind, subset, func = tup_arg
    r = subset.apply(func, axis=1)
    return (split_ind, r)

def df_multiprocess(df, processes, chunk_size, func, dataset_name):
    print("Generating parts...")
    with shelve.open('%s_%s_%s_results.tmp' % (dataset_name, func.__name__, chunk_size)) as results:
 
        pbar = tqdm(total=len(df), position=0)
        # Resume:
        finished_chunks = set([int(k) for k in results.keys()])
        pbar.desc = "Resuming"
        for k in results.keys():
            pbar.update(len(results[str(k)][1]))

        pool_data = ((index, df[i:i + chunk_size], func) for index, i in enumerate(range(0, len(df), chunk_size)) if index not in finished_chunks)
        print(int(len(df) / chunk_size), "parts.", chunk_size, "per part.", "Using", processes, "processes")
 
        pbar.desc = "Downloading"
        with Pool(processes) as pool:
            for i, result in enumerate(pool.imap_unordered(_df_split_apply, pool_data, 2)):
                results[str(result[0])] = result
                pbar.update(len(result[1]))
        pbar.close()

    print("Finished Downloading.")
    return

# Unique name based on url
def _file_name(row):
    return "%s/%s_%s" % (row['folder'], row.name, (zlib.crc32(row['url'].encode('utf-8')) & 0xffffffff))

# For checking mimetypes separately without download
def check_mimetype(row):
    if os.path.isfile(str(row['file'])):
        row['mimetype'] = magic.from_file(row['file'], mime=True)
        row['size'] = os.stat(row['file']).st_size
    return row

# Don't download image, just check with a HEAD request, can't resume.
# Can use this instead of download_image to get HTTP status codes.
def check_download(row):
    fname = _file_name(row)
    try:
        # not all sites will support HEAD
        response = requests.head(row['url'], stream=False, timeout=5, allow_redirects=True, headers=headers)
        row['status'] = response.status_code
        # row['headers'] = dict(response.headers)
    except:
        # log errors later, set error as 408 timeout
        row['status'] = 408
        return row
    if response.ok:
        row['file'] = fname
    return row

def download_image(row):
    fname = _file_name(row)
    # Skip Already downloaded, retry others later
    if os.path.isfile(fname):
        row['status'] = 200
        row['file'] = fname
        row['mimetype'] = magic.from_file(row['file'], mime=True)
        row['size'] = os.stat(row['file']).st_size
        return row

    try:
        # use smaller timeout to skip errors, but can result in failed downloads
        response = requests.get(row['url'], stream=False, timeout=10, allow_redirects=True, headers=headers)
        row['status'] = response.status_code
        #row['headers'] = dict(response.headers)
    except Exception as e:
        # log errors later, set error as 408 timeout
        row['status'] = 408
        return row
   
    if response.ok:
        try:
            with open(fname, 'wb') as out_file:
                # some sites respond with gzip transport encoding
                response.raw.decode_content = True
                out_file.write(response.content)
            row['mimetype'] = magic.from_file(fname, mime=True)
            row['size'] = os.stat(fname).st_size
        except:
            # This is if it times out during a download or decode
            row['status'] = 408
            return row
        row['file'] = fname
        
    return row

def open_tsv(fname, folder):
    print("Opening %s Data File..." % fname)
    df = pd.read_csv(fname, sep='\t', names=["caption","url"], usecols=range(1,2))
    df['folder'] = folder
    print("Processing", len(df), " Images:")
    return df

def df_from_shelve(chunk_size, func, dataset_name):
    print("Generating Dataframe from results...")
    with shelve.open('%s_%s_%s_results.tmp' % (dataset_name, func.__name__, chunk_size)) as results:
        keylist = sorted([int(k) for k in results.keys()])
        df = pd.concat([results[str(k)][1] for k in keylist], sort=True)
    return df

def merge_caption(caption_file_path, df):
    caption_file = pd.read_csv(caption_file_path, sep='\t', names=["caption","url"])
    df = df.merge(caption_file[["caption", "url"]], on="url", how="left")
    return df
    
# number of processes in the pool can be larger than cores
num_processes = 10
# chunk_size is how many images per chunk per process - changing this resets progress when restarting.
images_per_part = 100

data_name = "validation"
df = open_tsv("Validation_GCC-1.1.0-Validation.tsv", data_name)
df_multiprocess(df=df, processes=num_processes, chunk_size=images_per_part, func=download_image, dataset_name=data_name)
df = df_from_shelve(chunk_size=images_per_part, func=download_image, dataset_name=data_name)
print("Mapping Capture.")
df = merge_caption("Validation_GCC-1.1.0-Validation.tsv", df)
df.to_csv("downloaded_%s_report.tsv.gz" % data_name, compression='gzip', sep='\t', header=["file","folder","mimetype","size","status","url","caption"], index=False)
print("Saved.")

data_name = "training"
df = open_tsv("Train_GCC-training.tsv",data_name)
df_multiprocess(df=df, processes=num_processes, chunk_size=images_per_part, func=download_image, dataset_name=data_name)
df = df_from_shelve(chunk_size=images_per_part, func=download_image, dataset_name=data_name)
print("Mapping Capture.")
df = merge_caption("Train_GCC-training.tsv", df)
df.to_csv("downloaded_%s_report.tsv.gz" % data_name, compression='gzip', sep='\t', header=["file","folder","mimetype","size","status","url","caption"], index=False)
print("Saved.")
