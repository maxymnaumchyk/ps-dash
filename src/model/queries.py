import traceback
from elasticsearch.helpers import scan
from datetime import datetime
import pandas as pd

import utils.helpers as hp

import urllib3
urllib3.disable_warnings()



def convertDate(dt):
    return datetime.strptime(dt, "%Y-%m-%dT%H:%M:%S.000Z").strftime("%Y-%m-%dT%H:%M:%S.000Z")

# On 18 Nov 2023 all alarms moved to querying netsites instead of sites, 
# but kept src_site and dest_site for backward compatibility
def obtainFieldNames(dateFrom):
  target_date = datetime(2023, 11, 18)
  target_milliseconds = target_date.timestamp() * 1000

  if dateFrom > target_milliseconds:
    return 'src_netsite', 'dest_netsite'
  else:
    return 'src_site', 'dest_site'


def queryThroughputIdx(dateFrom, dateTo):
  # dateFrom = datetime.fromisoformat(dateFrom)
  # dateTo = datetime.fromisoformat(dateTo)
  query = {
    "bool": {
      "must": [
        {
          "range": {
            "timestamp": {
              "gt": dateFrom,
              "lte": dateTo,
              "format": "epoch_millis"
            }
          }
        },
        {
          "term": {
            "src_production": True
          }
        },
        {
          "term": {
            "dest_production": True
          }
        }
      ]
    }
  }

  
  src_field_name, dest_field_name = obtainFieldNames(dateFrom)

  aggregations = {
    "groupby": {
      "composite": {
        "size": 9999,
        "sources": [
          {
            "ipv6": {
              "terms": {
                "field": "ipv6"
              }
            }
          },
          {
            "src": {
              "terms": {
                "field": "src"
              }
            }
          },
          {
            "dest": {
              "terms": {
                "field": "dest"
              }
            }
          },
          {
            "src_host": {
              "terms": {
                "field": "src_host"
              }
            }
          },
          {
            "dest_host": {
              "terms": {
                "field": "dest_host"
              }
            }
          },
          {
            "src_site": {
              "terms": {
                "field": src_field_name
              }
            }
          },
          {
            "dest_site": {
              "terms": {
                "field": dest_field_name
              }
            }
          },
        ]
      },
      "aggs": {
        "throughput": {
          "avg": {
                        "field": "throughput"
                    }
                }
            }
        }
    }

    #     print(idx, str(query).replace("\'", "\""))
  aggrs = []

  aggdata = hp.es.search(index='ps_throughput', query=query, aggregations=aggregations, _source=False)
  for item in aggdata['aggregations']['groupby']['buckets']:
      aggrs.append({'hash': str(item['key']['src'] + '-' + item['key']['dest']),
                    'from': dateFrom, 'to': dateTo,
                    'ipv6': item['key']['ipv6'],
                    'src': item['key']['src'].upper(), 'dest': item['key']['dest'].upper(),
                    'src_host': item['key']['src_host'], 'dest_host': item['key']['dest_host'],
                    'src_site': item['key']['src_site'].upper(), 'dest_site': item['key']['dest_site'].upper(),
                    'value': item['throughput']['value'],
                    'doc_count': item['doc_count']
                    })

  return aggrs

def queryPathChanged(dateFrom, dateTo):
    # start = datetime.strptime(dateFrom, '%Y-%m-%dT%H:%M:%S.000Z')
    # end = datetime.strptime(dateTo, '%Y-%m-%dT%H:%M:%S.000Z')
    # if (end - start).days < 2:
    #   dateFrom, dateTo = hp.getPriorNhPeriod(dateTo)
    
    q = {
        "_source": [
            "from_date",
            "to_date",
            "src",
            "dest",
            "src_site",
            "dest_site",
            "diff"
        ],
        "query": {
          "bool": {
            "must": [{
              "range": {
                "to_date": {
                  "from": dateFrom,
                  "to": dateTo,
                  "format": "strict_date_optional_time"
                }
              }
            }]
          }
        }
    }
    # print(str(q).replace("\'", "\""))
    result = scan(client=hp.es, index='ps_traces_changes', query=q)
    data = []

    for item in result:
      temp = item['_source']
      temp['tag'] = [temp['src_site'], temp['dest_site']]
      temp['from'] = temp['from_date']
      temp['to'] = temp['to_date']
      del temp['from_date']
      del temp['to_date']
      data.append(temp)

    return data



def queryAlarms(dateFrom, dateTo):
  period = hp.GetTimeRanges(dateFrom, dateTo)
  q = {
        "query": {
            "bool": {
                "must": [
                    {
                        "range": {
                            "created_at": {
                                "gte": period[0],
                                "lte": period[1],
                                "format": "epoch_millis"
                            }
                        }
                    },
                    {
                        "term": {
                            "category": {
                                "value": "Networking",
                                "boost": 1.0
                            }
                        }
                    }
                ]
            }
        }
        }
  # print(str(q).replace("\'", "\""))
  try:
    result = scan(client=hp.es, index='aaas_alarms', query=q)
    data = {}

    for item in result:
        event = item['_source']['event']
        # if event != 'path changed':
        temp = []
        if event in data.keys():
            temp = data[event]

        if 'source' in item['_source'].keys():
          desc = item['_source']['source']
          if 'tags' in item['_source'].keys():
            tags = item['_source']['tags']
            desc['tag'] = tags

          if 'to' not in desc.keys():
            desc['to'] = datetime.fromtimestamp(item['_source']['created_at']/1000.0)

          if 'from' in desc.keys() and 'to' in desc.keys():
            desc['from'] = desc['from'].replace('T', ' ')
            desc['to'] = desc['to'].replace('T', ' ')

          if 'avg_value%' in desc.keys():
              desc['avg_value'] = desc['avg_value%']
              desc.pop('avg_value%')
          
          temp.append(desc)

          data[event] = temp

    # path changed details resides in a separate index
    pathdf = queryPathChanged(dateFrom, dateTo)
    data['path changed between sites'] = pathdf
    return data
  except Exception as e:
    print('Exception:', e)
    print(traceback.format_exc())



def getASNInfo(ids):
    query = {
        "query": {
            "terms": {
                "_id": ids
            }
        }
    }

    # print(str(query).replace("\'", "\""))
    asnDict = {}
    data = scan(hp.es, index='ps_asns', query=query)
    if data:
      for item in data:
          asnDict[str(item['_id'])] = item['_source']['owner']
    else:
        print(ids, 'Not found')

    return asnDict


# TODO: start querying form ps_meta
def getMetaData():
    meta = []
    data = scan(hp.es, index='ps_alarms_meta')
    for item in data:
        meta.append(item['_source'])

    if meta:
        return pd.DataFrame(meta)
    else:
        print('No metadata!')


def getAlarm(id):
  q = {
      "term": {
          "source.alarm_id": id
      }
  }
  data = []
  results = hp.es.search(index='aaas_alarms', size=100, query=q)
  for res in results['hits']['hits']:
    data.append(res['_source'])

  for d in data: print(d)
  if len(data) >0:
    return data[0]


def getCategory(event):

  q = {
      "term": {
          "event": event
      }
  }


  results = hp.es.search(index='aaas_categories', query=q)

  for res in results['hits']['hits']:
    return res['_source']
  

def getSubcategories():
  # TODO: query from aaas_categories when the alarms are reorganized
  # q = {
  #       "query": {
  #           "term": {
  #             "category": "Networking"
  #         }
  #       }
  #     }

  # results = scan(hp.es, index='aaas_categories', query=q)

  # subcategories = []

  # for res in results:
  #     subcategories.append({'event': res['_source']['event'], 'category': res['_source']['subcategory']})
  # # "path changed between sites" is not in aaas_alarms, because the alarms are too many
  # # the are gouped under an ASN in path_changed alarm
  # subcategories.append({'event': 'path changed between sites', 'category': 'RENs'})

  # catdf = pd.DataFrame(subcategories)

  description = {
  'Infrastructure': 	['bad owd measurements','large clock correction', 'path changed between sites', 
	 				            'destination cannot be reached from multiple', 'destination cannot be reached from any',
			                'source cannot reach any', 'firewall issue'],
  'Network': 		      ['bandwidth decreased from/to multiple sites', 'bandwidth decreased'],
  'Other': 		      ['bandwidth increased from/to multiple sites', 'bandwidth increased',
             		      'high packet loss', 'high packet loss on multiple links', 'complete packet loss']
  }

  subcategories = []
  for cat, ev in description.items():
    for e in ev:
      subcategories.append({'category': cat, 'event': e})

  catdf = pd.DataFrame(subcategories)

  return catdf


def queryTraceChanges(dateFrom, dateTo):
  # dateFrom = convertDate(dateFrom)
  # dateTo = convertDate(dateTo)

  q = {
    "query": {
      "bool": {
        "must": [
          {
            "range": {
              "to_date": {
                "from": dateFrom,
                "to": dateTo,
                "format": "strict_date_optional_time"
              }
            }
          }
        ]
      }
    }
  }

  # print(str(q).replace("\'", "\""))
  result = scan(client=hp.es,index='ps_traces_changes',query=q)
  data, positions, baseline, altPaths = [],[],[],[]
  positions = []
  for item in result:
      # print(len(data))
      item['_source']['src'] = item['_source']['src'].upper()
      item['_source']['dest'] = item['_source']['dest'].upper()
      item['_source']['src_site'] = item['_source']['src_site'].upper()
      item['_source']['dest_site'] = item['_source']['dest_site'].upper()

      tempD = {}
      for k,v in item['_source'].items():
          if k not in ['positions', 'baseline', 'alt_paths', 'created_at']:
              tempD[k] = v
      data.append(tempD)

      src = item['_source']['src']
      dest = item['_source']['dest']
      src_site = item['_source']['src_site']
      dest_site = item['_source']['dest_site']
      from_date,to_date = item['_source']['from_date'], item['_source']['to_date']

      temp = item['_source']['positions']
      for p in temp:
          p['src'] = src
          p['dest'] = dest
          p['src_site'] = src_site
          p['dest_site'] = dest_site
          p['from_date'] = from_date
          p['to_date'] = to_date
      positions.extend(temp)
      
      temp = item['_source']['baseline']
      for p in temp:
          p['src'] = src
          p['dest'] = dest
          p['src_site'] = src_site
          p['dest_site'] = dest_site
          p['from_date'] = from_date
          p['to_date'] = to_date
      baseline.extend(temp)

      temp = item['_source']['alt_paths']
      for p in temp:
          p['src'] = src
          p['dest'] = dest
          p['src_site'] = src_site
          p['dest_site'] = dest_site
          p['from_date'] = from_date
          p['to_date'] = to_date
      altPaths.extend(temp)

  df = pd.DataFrame(data)
  posDf = pd.DataFrame(positions)
  baseline = pd.DataFrame(baseline)
  altPaths = pd.DataFrame(altPaths)
  
  if len(df) > 0:
    posDf['pair'] = posDf['src']+' -> '+posDf['dest']
    df['pair'] = df['src']+' -> '+df['dest']
    baseline['pair'] = baseline['src']+' -> '+baseline['dest']
    altPaths['pair'] = altPaths['src']+' -> '+altPaths['dest']

  return df, posDf, baseline, altPaths


def query4Avg(idx, dateFrom, dateTo):
  # TODO: stick to 1 date format
  # dateFrom = convertDate(dateFrom)
  # dateTo = convertDate(dateTo)
  val_fld = hp.getValueField(idx)
  src_field_name, dest_field_name = obtainFieldNames(dateFrom)
  query = {
            "size" : 0,
            "query" : {
              "bool" : {
                "must" : [
                  {
                    "range" : {
                      "timestamp" : {
                        "gt" : dateFrom,
                        "lte": dateTo,
                        "format": "epoch_millis"
                      }
                    }
                  },
                  {
                    "term" : {
                      "src_production" : True
                    }
                  },
                  {
                    "term" : {
                      "dest_production" : True
                    }
                  }
                ]
              }
            },
            "aggregations" : {
              "groupby" : {
                "composite" : {
                  "size" : 9999,
                  "sources" : [
                    {
                      "src" : {
                        "terms" : {
                          "field" : "src"
                        }
                      }
                    },
                    {
                      "dest" : {
                        "terms" : {
                          "field" : "dest"
                        }
                      }
                    },
                    {
                      "src_site" : {
                        "terms" : {
                          "field" : src_field_name
                        }
                      }
                    },
                    {
                      "dest_site" : {
                        "terms" : {
                          "field" : dest_field_name
                        }
                      }
                    },
                    {
                      "src_host" : {
                        "terms" : {
                          "field" : "src_host"
                        }
                      }
                    },
                    {
                      "dest_host" : {
                        "terms" : {
                          "field" : "dest_host"
                        }
                      }
                    }
                  ]
                },
                "aggs": {
                  val_fld: {
                    "avg": {
                      "field": val_fld
                    }
                  }
                }
              }
            }
          }

  aggrs = []

  aggdata = hp.es.search(index=idx, body=query, _source=False)
  for item in aggdata['aggregations']['groupby']['buckets']:
      aggrs.append({'pair': str(item['key']['src']+'-'+item['key']['dest']),
                    'src': item['key']['src'], 'dest': item['key']['dest'],
                    'src_host': item['key']['src_host'], 'dest_host': item['key']['dest_host'],
                    'src_site': item['key']['src_site'], 'dest_site': item['key']['dest_site'],
                    'value': item[val_fld]['value'],
                    'from': dateFrom, 'to': dateTo,
                    'doc_count': item['doc_count']
                    })

  return aggrs