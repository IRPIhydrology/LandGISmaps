import pandas, requests
from glob import glob
import os.path as osp
import subprocess
import re
import pprint
import datetime
from osgeo import gdal

import ee
from ee.batch import Task

ee.Initialize()

input_fld = '/data/layers/layers_to_display/'
tmp_fld = '/content/tmp_data/'
gcs_bucket = 'landgis_ogh'
#gcs_bucket = 'landgis'
#gcs_path = 'gs://landgis_ogh/'
gee_path = 'users/josipkrizan/landgis'

#layer_table_path = 'https://raw.githubusercontent.com/Envirometrix/LandGISmaps/master/tables/LandGIS_tables_landgis_layers.csv'
#layer_table_path = '/content/LandGIS_tables - landgis_layers.csv'
layer_table_path = 'https://docs.google.com/spreadsheets/d/1ZmFa2R9mfiMxoOOP_ISAF6Ca5QMFJYL54pV-b_4zHTU/export?gid=30363016&format=csv'

layer_table = pandas.read_csv(layer_table_path,sep=',',dtype={'layer_unique_number':str})
# Only public layers
layer_table = layer_table.loc[layer_table.layer_public_download==1]

# GDAL_TRANSLATE OPTIONS
# TILED=YES; BLOCKXSIZE=512; BLOCKYSIZE=512
# "COPY_SRC_OVERVIEWS=NO" -- no need it's default
#gdal_options={'F': ["COMPRESS=LZW","PREDICTOR=2","NUM_THREADS=ALL_CPUS","BIGTIFF=YES"],
#              'N': ["COMPRESS=LZW","PREDICTOR=2","NUM_THREADS=ALL_CPUS","BIGTIFF=YES"]}
gdal_options=["COMPRESS=LZW","PREDICTOR=2","NUM_THREADS=ALL_CPUS","BIGTIFF=YES","TILED=YES","BLOCKXSIZE=512","BLOCKYSIZE=512"]
gdal_config=["GDAL_DISABLE_READDIR_ON_OPEN TRUE","GDAL_CACHEMAX 5000"]

# Read table into pandas dataframe
# layer_table = pandas.read_table(layer_table_path,sep=',',dtype={'layer_unique_number':str})

#def month_to_number(month):
#    months = dict(jan=1, feb=2, mar=3, apr=4, may=5, jun=6, jul=7, aug=8, sep=9, oct=10, nov=11, dec=12, annual=13, annualdiff=14)
#    return months.get(month,0)
  
def find_month(desc):
    months = dict(jan=1, feb=2, mar=3, apr=4, may=5, jun=6, jul=7, aug=8, sep=9, oct=10, nov=11, dec=12, annual=13, annualdiff=14)
    for month in months.keys():
        if month in re.split(r'[\.\_]+',desc):
            return month, months[month]
    return desc, 0
  
def dt_convert_msepoch_year(year):
    import datetime, calendar
    if year<=0:
        return None
    
    dt1=datetime.datetime(year,1,1).timetuple()
    dt2=datetime.datetime(year+1,1,1).timetuple()
    e1 = int(calendar.timegm(dt1) * 1000) #+ dt1.microsecond / 1000--Ovo je ionako 0
    e2 = int(calendar.timegm(dt2) * 1000) #+ dt2.microsecond / 1000
    return e1,e2

def dt_convert_msepoch(date_string):
    import datetime, calendar
    dt=datetime.datetime.strptime(date_string,'%Y-%m-%d')
    return int(calendar.timegm(dt.timetuple()) * 1000) + dt.microsecond / 1000

def modified_time(filename):
    import datetime, os
    if os.path.exists(filename):
      return datetime.datetime.fromtimestamp(os.stat(filename).st_mtime,datetime.timezone.utc)
    else:
      return None

def gcs_modified_time(gcs_id):
    from google.cloud import storage
    client = storage.Client()
    bucket = client.get_bucket(gcs_bucket)
    blob = bucket.blob(gcs_id)    
    if (blob.exists()):        
        blob.reload()
        return blob.updated
    else:
        return None

def gee_modified_time(gee_id):
  import pytz
  gee_info = ee.data.getInfo(gee_id)
  #print(ee.Date(int(gee_info['version'])//1000).format("y-MM-dd HH-mm-ss").getInfo())
  if gee_info is None:
    return None
  else:
    mt = datetime.datetime.strptime(ee.Date(int(gee_info['version'])//1000).format("y-MM-dd HH-mm-ss").getInfo(),"%Y-%m-%d %H-%M-%S")
    mt = mt.replace(tzinfo=pytz.UTC)
    return mt

def gee_running_tasks():
    tl = Task.list()
    tl_running = filter(lambda x: x.state ==Task.State.RUNNING and x.task_type=='INGEST', tl)
    tl_running = map(lambda x: dict(id=x['id'], asset_id=x['description'].split(':')[1].strip()), tl_running)
    #pprint.pprint(list(tl_running))
    return tl_running

def raster_type(filename):
  from osgeo import gdal
  raster = gdal.Open(filename)
  band = raster.GetRasterBand(1)
  return band.DataType
  #print(band.DataType)
  #print(gdal.GetDataTypeName(band.DataType))
  

def layer_metadata(layer_unique_number):
    row = layer_table.loc[layer_table.layer_unique_number==layer_unique_number].iloc[0]
    props={}    
    props['version'] = osp.splitext(osp.basename(row.layer_filename_pattern))[0].split('_')[-1]
    props['title'] = row['layer_title']
    props['description'] = '{}\n<br>\n<a href="{}"> description </a>'.format(row['layer_title_description'], row['layer_description_url'])
    props['system:description'] = props['description']
    props['provider'] = row.layer_organization
    props['provider_url'] = 'https://landgis.opengeohub.org/'
    
    if row.layer_gee_product_list is not None:
        props['product_tags'] = row.layer_gee_product_list  #list(row.layer_gee_product_list.split(','))
    
    #props['sample']=''# https://maps.opengeohub.org/uploaded/thumbs/layer-48c98d30-6bfd-4fb3-a2e4-c414ac4ce3aa-thumb.png 
    #props['thumb'] = '' # ISTO, treba naći id od geoserver layera (pomoću WMSA?)
    filename_pattern = osp.join(input_fld, row.layer_distribution_folder, row.layer_filename_pattern.replace('*..*','*')).strip()
    #if layer_unique_number==6.5:
    #    filename_pattern = filename_pattern.replace('v0.2', 'v0.1')
    type_spatial, type_value = row.layer_display_type.split('_')
    
    ret={'properties':props}
    ret['row'] = row.to_dict()
    ret['public'] = row.layer_public_download==1
    ret['layer_unique_number'] = row.layer_unique_number
    ret['filename_pattern'] = filename_pattern
    ret['type_spatial']=type_spatial
    ret['type_value']=type_value    
    ret['gee_id'] = row.layer_filename_shortname.replace('.*','').replace('.','-').upper()
    ret['gee_full_id'] = osp.join(gee_path, ret['gee_id'])
    ret['gee_type'] = 'image'
    ret['gcs_filename'] = osp.join('gs://' + gcs_bucket,ret['gee_id']+'.tif')
    ret['gcs_source_filename'] = filename_pattern
    
    # Fining time_start and time_end
    if type_spatial != 'TS':
      m = re.findall(r'_(\d+)\.\.(\d+)_v', filename_pattern)
      if len(m)>0:
          year_start, year_end = m[0]
      else:
          m = re.findall(r'_(\d+)_v', filename_pattern)
          if m is not None:
              year_start = m[0]
              year_end = year_start
      if m is not None:
          time_start = '{}-01-01'.format(year_start)
          time_end = '{}-12-31'.format(year_end)
          ret['properties']['system:time_start']=dt_convert_msepoch(time_start)
          ret['properties']['system:time_end']=dt_convert_msepoch(time_end)
    
    #if ret['gee_type']=='image':
    ret['gcs_id'] = ret['gee_id']+'.tif'
    #else:
    #  ret['gcs_id'] = ret['gee_id']
    ret['gcs_updated'] = gcs_modified_time(ret['gcs_id'])
    
    ret['need_tmp']=False
    ret['gdal_type']=None
    if type_spatial=='3D':      
        layers = glob(filename_pattern)
        #print(filename_pattern)
        sublayers=[]
        for fp in layers:
            bn = osp.basename(fp)
            m = re.findall(r'(\d+)cm', bn)
            depth = int(m[0])            
            sublayers.append({'depth': depth, 'filename':fp, 'band_name': 'b{}'.format(depth), 'gdal_type':raster_type(fp), 'updated':modified_time(fp)})
            # dest_name='{}_{}cm'.format(layer_name, depth).replace('.','_')      
        ret['sublayers']=sorted(sublayers, key=lambda x: x['depth'])
        ret['updated'] = max([s['updated'] for s in sublayers]) if len(sublayers)>0 else None
        ret['gdal_type'] = max([s['gdal_type'] for s in sublayers]) if len(sublayers)>0 else None
        ret['need_tmp'] = True
    elif type_spatial=='SS':      
        layers = glob(filename_pattern)
        sublayers=[(l, find_month(osp.basename(l).split('_')[2])) for l in layers]
        sublayers=[{'filename':l[0], 'band_name':l[1][0], 'month_number':l[1][1] , 'gdal_type':raster_type(l[0]),'updated':modified_time(l[0])} for l in sublayers]
        sublayers=sorted(sublayers, key = lambda x: x['month_number'])
        ret['sublayers'] = sublayers
        ret['updated'] = max([s['updated'] for s in sublayers]) if len(sublayers)>0 else None
        ret['gdal_type'] = max([s['gdal_type'] for s in sublayers]) if len(sublayers)>0 else None
        ret['need_tmp'] =True         
    elif type_spatial=='TS':
        layers = glob(filename_pattern)
        sublayers=sorted([(l, osp.basename(l).split('_')[-2]) for l in layers], key=lambda x: x[1])        
        sublayers=[{'filename':l[0],'year':int(l[1].replace('BC','-')), 'gee_id':l[1], 'gdal_type':raster_type(l[0]), 'updated':modified_time(l[0])} for l in sublayers]
        ret['sublayers'] = sorted(sublayers,key=lambda x:x['year'])
        ret['updated'] = max([s['updated'] for s in sublayers]) if len(sublayers)>0 else None
        ret['gdal_type'] = max([s['gdal_type'] for s in sublayers]) if len(sublayers)>0 else None
        ret['gee_type'] = 'imagecollection'
        ret['gcs_updated'] = gcs_modified_time(ret['gcs_id']+'/'+osp.basename(ret['sublayers'][-1]['filename']))
    elif type_spatial=='2D':
        ret['updated'] = modified_time(ret['filename_pattern'])

    if 'sublayers' in ret:
        wms_layer_name = "{}:{}".format(row['layer_distribution_folder'],osp.splitext[osp.basename(ret['sublayers'][0]['filename'])][0])
    else:
        wms_layer_name = "{}:{}".format(row['layer_distribution_folder'],osp.splitext(row.layer_filename_pattern)[0])
    ret['properties']['thumbs'] = "https://geoserver.opengeohub.org/landgisgeoserver/wms/reflect?layers={}&format=image/png8&width=256".format(wms_layer_name)
        
    if ret['need_tmp']:
      lun=ret['layer_unique_number']
      gee_id = ret['gee_id']
      vrt_sources=osp.join(tmp_fld,'{}_{}.txt'.format(lun,gee_id))
      vrt_filename=osp.join(tmp_fld,'{}_{}.vrt'.format(lun,gee_id))
      tmp_filename=osp.join(tmp_fld,'{}_{}.tif'.format(lun,gee_id))
      if osp.exists(tmp_filename):
        tmp_updated=modified_time(tmp_filename)
      else:
        tmp_updated=None
      ret['tmp_specs'] = dict(vrt_sources=vrt_sources, vrt_filename=vrt_filename, tmp_filename=tmp_filename, tmp_updated=tmp_updated)
      ret['gcs_source_filename'] = tmp_filename
      
    
    
    ret['properties']['updated']=ret['updated'].strftime('%Y%m%d%H%M%S') if ret['updated'] is not None else None
    return ret

def run_cmd(cmd):
    #print("COMMAND: {}".format(' '.join(cmd)))
    cp = subprocess.run(cmd, universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(cp.stdout)
    
def make_tmp(md, force=False, return_command=False):
  if not md['need_tmp']:
    return 
  
  if not force:
    if md['tmp_specs']['tmp_updated'] is not None:
      if md['tmp_specs']['tmp_updated'] > md['updated']:
        print ('Tmp file is newer then source file. Skipping building tmp file!')
        return
      
  # First we need to merge all sublayers into one image
  sources = [l['filename'] for l in md['sublayers']]
  mdtmp = md['tmp_specs']
  vrt_sources=mdtmp['vrt_sources']   #osp.join(tmp_fld,'{}_{}.txt'.format(lun,gee_id))
  vrt_filename=mdtmp['vrt_filename'] #osp.join(tmp_fld,'{}_{}.vrt'.format(lun,gee_id))
  tmp_filename=mdtmp['tmp_filename']  #osp.join(tmp_fld,'{}_{}.tif'.format(lun,gee_id))
  #gcs_filename=md['gcs_filename']
                  
  
  open(vrt_sources,'w').write('\n'.join(sources))    
  run_cmd(['gdalbuildvrt','-separate','-input_file_list',vrt_sources,vrt_filename])    
  
  cmd = ['gdal_translate',vrt_filename,tmp_filename]
  if md['gdal_type'] is not None:
    cmd.extend(['-ot', gdal.GetDataTypeName(md['gdal_type'])])
  for opt in gdal_options: #[md['type_value']]:
    cmd.extend(['-co', opt])
  for opt in gdal_config:
    cmd.extend(['--config']+opt.split(" "))
    
  #if md['unique_layer_number']=='7.2':
  #  cmd.extend(['-ot','Int16'])
       
  if return_command:
    return ' '.join(cmd)
  else:
    run_cmd(cmd)  
    md['tmp_specs']['tmp_updated']=modified_time(tmp_filename)
                                    
def gcs_upload(md, force=False):
  if not force:
    if md['need_tmp']:
      if md['gcs_updated'] is not None:
        if md['gcs_updated'] > md['tmp_specs']['tmp_updated']:
          print('GCS file is newer then tmp file. Skipping upload to GCS!')
    else:
      if md['gcs_updated'] is not None:
        if md['gcs_updated']>md['updated']:
          print('GCS file is newer then filesystem file. Skipping upload to GCS!')
          return
    
    run_cmd(['gsutil','cp',md['gcs_source_filename'], md['gcs_filename']])
    
    
def gee_upload_imagecollection(md):
    gee_id_col = md['gee_full_id']
    #pprint.pprint(md)

    res_ids=[]
    pyramidingPolicy = 'MODE' if md['type_value']=='F' else None
    if gee_modified_time(gee_id_col) is None:
        res = ee.data.createAsset({'type':ee.data.ASSET_TYPE_IMAGE_COLL}, gee_id_col, opt_force=True, opt_properties=md['properties'])
    for s in md['sublayers']:    
        properties = md['properties'].copy()
        properties['year'] = s['gee_id']
        '''
        if s['year']>=1000:        
            properties['system:start_time'], properties['system:end_time'] = dt_convert_msepoch_year(s['year'])
        else:
            properties.pop('system:start_time',None)
            properties.pop('system:end_time',None)
        ''' 
        gee_id = gee_id_col+'/'+s['gee_id']
        gcs_id = md['gcs_filename']+'/'+osp.basename(s['filename'])
        #print(gee_id,gcs_id)
        if gee_modified_time(gee_id) is None:
            res = gee_upload_image(gee_id, gcs_id, properties, bands=None)

        res_ids.append(res['id'])
                                      
    return res_ids
    
def gee_upload_image(gee_id, gcs_filename, properties, bands=None, pyramidingPolicy=None):
    params={}
    params['id'] = gee_id 

    sources = [{'primaryPath': gcs_filename}]
    params['tilesets'] = [{'sources': sources}]
    
    if pyramidingPolicy is not None:
        params['pyramidingPolicy'] = pyramidingPolicy #'MODE' 
  
    if bands is not None:
        params['bands'] = bands #[{'id':l['band_name']} for l in md['sublayers']]
  
    params['properties']=properties

    newId = ee.data.newTaskId()[0]
    ret = ee.data.startIngestion(newId, params)    
    return ret

def gee_upload(md, force=False):    
    mt_gcs = md['gcs_updated']
    gee_full_id = md['gee_full_id']
    if mt_gcs is None:
      print("No GCS file to upload!")
      return
    
    mt = gee_modified_time(gee_full_id)
    
    if not force:
      if mt is not None and mt_gcs is not None:
        if mt>=mt_gcs:
            print('GEE asset is newer or same GCS file. Skipping upload to GEE')
            return
        
    rt = list(filter(lambda x: x['asset_id']==gee_full_id, gee_running_tasks()))
    
    if len(rt)>0: # There are alredy ingestion of this asset in progress
        print('Ingestion of {} allready in progress. '.format(md['gee_id']), end='')
        if force:
            print('Canceling previous task.')
            ee.data.cancelTask(rt[0]['id'])
        else:
            print('Skipping upload.')
            return

    if mt is not None:
        print('GEE asset exists. Deleting.')
        ee.data.deleteAsset(gee_full_id)
    
    if md['gee_type']=='imagecollection':
        return gee_upload_imagecollection(md)
    elif md['gee_type']=='image':
        bands = [{'id':l['band_name']} for l in md['sublayers']] if md['need_tmp'] else None
        pyramidingPolicy = 'MODE' if md['type_value']=='F' else None
        return gee_upload_image(gee_full_id, md['gcs_filename'], md['properties'], bands, pyramidingPolicy)
    

def gee_update_properties(md):    
    
    mt = gee_modified_time(md['gee_full_id'])
    if mt is None:      
      return "GEE asset does not exists"
                   
    ee.data.setAssetProperties(md['gee_full_id'], md['properties'])
    return "OK"
  

def make_all_3DSS():
  import stat, os
  
  err=[]
  sh_file = osp.join(tmp_fld,'make_3dss.sh')
  with open(sh_file,'w') as fid:
    fid.write('#!/bin/bash\n')
    for lun in layer_table.layer_unique_number:
      print(lun)
      md = layer_metadata(lun)
      if md['updated']==None:
        print('File does not exists: {}'.format(md['filename_pattern']))
        err.append(md['filename_pattern'])
        continue
        
      if md['row']['layer_public_download']==0:
        continue
      if not md['need_tmp']:
        continue      
      
      print(lun, md['gee_id'])
      cmd = make_tmp(md,return_command=True)
      if cmd is not None:
        fid.write('echo "{}_{}"\n'.format(lun,md['gee_id']))
        fid.write('{}\n'.format(cmd))
    
  print('\n'.join(err))
  st = os.stat(sh_file)
  os.chmod(sh_file, st.st_mode | stat.S_IEXEC)
  
def upload_all_gcs():
  for lun in layer_table.layer_unique_number:
    md=layer_metadata(lun)
    print(lun)
    gcs_upload(md)

def upload_all_gee():
  for lun in layer_table.layer_unique_number:
    md=layer_metadata(lun)
    print(lun,' ',end='')
    r = gee_upload(md)
    if r is None:
      continue
    if isinstance(r,list):
        print (r)
        return
    if r['started']=='OK':
      print(" .. started .. "+r['id'])
    else:
      print(" .. failed  ..")
      
def set_all_properties_gee():
  for lun in layer_table.layer_unique_number:
    md = layer_metadata(lun)
    print(lun,' ',end='')
    print(gee_update_properties(md))
    

if __name__=='__main__':
    #make_all_3DSS()
    #upload_all_gcs()
    #upload_all_gee()
    #set_all_properties_gee()
    pass