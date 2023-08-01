#!/usr/bin/env python3                                                         

import time, argparse
import numpy as np
import arkouda as ak
import random
import string
import arachne_development.lcs as njit


def time_ak_lcs( strlen1, strlen2,trials):

    stringsOne = ak.random_strings_uniform(minlen=strlen1-1, maxlen=strlen1, seed=1,\
                  size= 1, characters="printable")
    stringsTwo = ak.random_strings_uniform(minlen=strlen2-1, maxlen=strlen2, seed=1, \
                  size=1, characters="printable")

    print(stringsOne)
    print(stringsOne.size)
    print(stringsOne.nbytes)
    print(stringsOne.ndim)
    print(stringsOne.shape)
    print(stringsOne.dtype)
    print(stringsTwo)
    print(stringsTwo.size)
    print(stringsTwo.nbytes)
    print(stringsTwo.ndim)
    print(stringsTwo.shape)
    print(stringsTwo.dtype)

    timings = []
    for _ in range(trials):
        start = time.time()
        c=njit.lcs(stringsOne,stringsTwo)
        end = time.time()
        timings.append(end - start)
    tavg = sum(timings) / trials

    print("Average time = {:.4f} sec".format(tavg))


def create_parser():
    parser = argparse.ArgumentParser(description="Measure the performance of suffix array building: C= suffix_array(V)")
    parser.add_argument('hostname', help='Hostname of arkouda server')
    parser.add_argument('port', type=int, help='Port of arkouda server')
    parser.add_argument('--len1', default=20, help='length of string 1')
    parser.add_argument('--len2', default=10, help='length of string 2')
    parser.add_argument('-t', '--trials', type=int, default=6, help='Number of times to run the benchmark')
    return parser


    
if __name__ == "__main__":
    import sys
    parser = create_parser()
    args = parser.parse_args()
    ak.connect(args.hostname, args.port)

    time_ak_lcs(args.len1, args.len2, args.trials)
    ak.shutdown()
    sys.exit(0)
