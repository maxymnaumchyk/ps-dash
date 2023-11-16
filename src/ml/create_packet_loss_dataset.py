import pandas as pd

import model.queries as qrs
import utils.helpers as hp
from utils.helpers import timer

@timer
def loadPacketLossData(dateFrom, dateTo):
    data = []
    intv = int(hp.CalcMinutes4Period(dateFrom, dateTo) / 60)
    time_list = hp.GetTimeRanges(dateFrom, dateTo, intv)
    for i in range(len(time_list) - 1):
        # print('packetloss query', time_list[i], time_list[i + 1])
        data.extend(qrs.query4Avg('ps_packetloss', time_list[i], time_list[i + 1]))

    return pd.DataFrame(data)


def getPercentageMeasuresDone(df, dateFrom, dateTo):
    measures_done = df.groupby('pair').agg({'doc_count': 'sum'})

    def findRatio(row, total_minutes):
        if pd.isna(row['doc_count']):
            count = '0'
        else:
            count = str(round((row['doc_count'] / total_minutes) * 100)) + '%'
        return count

    one_test_per_min = hp.CalcMinutes4Period(dateFrom, dateTo)
    measures_done['tests_done'] = measures_done.apply(
        lambda x: findRatio(x, one_test_per_min), axis=1)
    df = pd.merge(df, measures_done, on='pair', how='left')

    return df


@timer
def markPairs(dateFrom, dateTo):
    dataDf = loadPacketLossData(dateFrom, dateTo)
    dataDf = dataDf[~dataDf['value'].isnull()]
    df = getPercentageMeasuresDone(dataDf, dateFrom, dateTo)

    # set value to 0 - we consider there is no issue bellow 2% loss
    # set value to 1 - the pair is marked problematic between 2% and 100% loss
    # set value to 2 - the pair shows 100% loss
    def setFlag(x):
        if x >= 0 and x < 0.02:
            return 0
        elif x >= 0.02 and x < 1:
            return 1
        elif x == 1:
            return 2
        return 'something is wrong'
    
    df['flag'] = df['value'].apply(lambda val: setFlag(val))
    df.rename(columns={'value': 'avg_value'}, inplace=True)
    df = df.round({'avg_value': 3})

    return df


"""
'src', 'src_host', 'src_site':      info about the source
'dest', 'dest_host', 'dest_site':   info about the destination
'avg_value':                        the average value for the pair
'tests_done':                       % of tests done for the whole period. The calculation is based on the assumption
                                    that there should be 1 measure per minute
"""
@timer
def createPcktDataset(dateFrom, dateTo):
    # dateFrom, dateTo = ['2023-10-01 03:00', '2023-10-03 03:00']
    plsDf = markPairs(dateFrom, dateTo)
    plsDf = plsDf[plsDf['tests_done'] != '0%']

    plsDf['src_site'] = plsDf['src_site'].str.upper()
    plsDf['dest_site'] = plsDf['dest_site'].str.upper()

    return plsDf