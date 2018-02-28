import pkg_resources

def earlier_version(a, b):
    v = pkg_resources.parse_version
    # parse_version doesn't really support '+' (1.0+1 < 1.0-1)
    if '+' in a or '+' in b:
        def split(a):
            ia = a.find('+')
            if ia == -1:
                ia = a.find('-')
            if ia != -1:
                return a[:ia], a[ia], a[ia + 1:]
            return a, '', ''
        a = split(a)
        b = split(b)

        
        print(a, b)
        va = v(a[0])
        vb = v(b[0])
        if va != vb:
            return va < vb

        v_magnitute = ['-', '', '+']
        if a[1] != b[1]:
            v = [v_magnitute.index(s) for s in (a[1], b[1])]
            return v[0] < v[1]
        return v(a[2]) < v(b[2])
    else:
        return pkg_resources.parse_version(a) < pkg_resources.parse_version(b)
